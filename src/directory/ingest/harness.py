import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Protocol

_POSIX = os.name == "posix"


@dataclass
class HarnessResult:
    text: str
    model: str
    ok: bool
    error: str | None = None


class _Aborted(RuntimeError):
    """Raised by the process runner once shutdown has been latched, so a pending
    harness call returns a failed result instead of spawning a fresh agent."""


class _ProcessManager:
    """Tracks live agent subprocesses so a single Ctrl-C can tear down the whole
    tree. Each agent is spawned in its own session/process-group; ``shutdown``
    signals every group (SIGTERM, then SIGKILL for stragglers) so no orphaned
    ``claude`` — or the tools it spawned — keeps running and burning tokens after
    the operator interrupts a long authoring batch."""

    def __init__(self) -> None:
        self._live: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

    @property
    def shutting_down(self) -> bool:
        return self._shutdown.is_set()

    def reset(self) -> None:
        """Clear the shutdown latch for a fresh run (and between tests)."""
        self._shutdown.clear()

    def _register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._live.add(proc)

    def _unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._live.discard(proc)

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        try:
            if _POSIX:
                os.killpg(os.getpgid(proc.pid), sig)
            else:  # pragma: no cover - non-POSIX fallback
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # already dead, or we lost the right to signal it

    def shutdown(self, *, grace: float = 0.5) -> int:
        """Latch shutdown and terminate every live agent process group. Returns
        the number of process groups that were signalled."""
        self._shutdown.set()
        with self._lock:
            live = list(self._live)
        for proc in live:
            self._signal(proc, signal.SIGTERM)
        if live:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and any(p.poll() is None for p in live):
                time.sleep(0.05)
            for proc in live:
                if proc.poll() is None:
                    self._signal(proc, signal.SIGKILL)
        return len(live)

    def run(self, cmd, *, capture_output=True, text=True, timeout=None, cwd=None):
        """``subprocess.run``-compatible runner that registers the child for group
        termination. Refuses to spawn once shutdown has been latched, and never
        leaks the child if the call is interrupted or times out."""
        if self._shutdown.is_set():
            raise _Aborted("authoring shutdown in progress")
        pipe = subprocess.PIPE if capture_output else None
        kwargs = {}
        if _POSIX:
            # Own session/group so killpg reaches the agent *and* its child tools.
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, stdout=pipe, stderr=pipe, text=text, cwd=cwd, **kwargs)
        self._register(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            # Timeout, Ctrl-C on this thread, or any abort: kill the whole group
            # and reap it before re-raising so nothing is left running.
            self._signal(proc, signal.SIGKILL)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            self._unregister(proc)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


_processes = _ProcessManager()


def request_shutdown() -> int:
    """Latch shutdown and terminate all in-flight agent subprocesses. Returns the
    number of process groups signalled. Idempotent."""
    return _processes.shutdown()


def is_shutting_down() -> bool:
    """True once :func:`request_shutdown` has latched (workers should stop)."""
    return _processes.shutting_down


def reset_shutdown() -> None:
    """Clear the shutdown latch so a new run can spawn agents again."""
    _processes.reset()


class AuthorHarness(Protocol):
    name: str

    def run(self, prompt: str, *, model: str) -> HarnessResult: ...


def _split_effort(model: str) -> tuple[str, str | None]:
    """Split a ``model[@effort]`` spec into (model_id, effort). ``opus@low`` →
    ``("opus", "low")``; a bare ``opus`` → ``("opus", None)``. Lets the funnel pass
    a per-attempt reasoning effort through the single ``model`` channel."""
    model_id, sep, effort = model.partition("@")
    return model_id, (effort or None) if sep else None


def _with_budget(prompt: str, page_budget: int, token_budget: int) -> str:
    """Append the advisory page/token budget directive shared by both agentic
    fallbacks (the agent emits ``{}`` on overrun, which fails config validation)."""
    return (
        f"{prompt}\n\nBudget: visit at most {page_budget} pages and "
        f"{token_budget} tokens. If you exceed either, stop and output exactly {{}}."
    )


class _SubprocessHarness:
    """Shared subprocess core for CLI-backed harnesses. Subclasses supply the argv
    via ``_command`` and may massage the prompt via ``_prepare``.
    ``binary``/``timeout``/``runner`` are injectable so the call is unit-testable
    without spawning a real agent."""

    name = "cli"
    _cwd: str | None = None

    def __init__(self, *, binary: str, timeout: float = 180.0, runner=_processes.run) -> None:
        self._binary = binary
        self._timeout = timeout
        self._runner = runner

    def _prepare(self, prompt: str) -> str:
        return prompt

    def _command(self, prompt: str, model: str) -> list[str]:
        raise NotImplementedError

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        kwargs = {"capture_output": True, "text": True, "timeout": self._timeout}
        if self._cwd:
            kwargs["cwd"] = self._cwd
        try:
            proc = self._runner(self._command(self._prepare(prompt), model), **kwargs)
        except _Aborted:
            return HarnessResult("", model, False, error="aborted")
        except (OSError, subprocess.SubprocessError) as exc:
            return HarnessResult("", model, False, error=f"{type(exc).__name__}: {exc}")
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
            return HarnessResult("", model, False, error=err)
        return HarnessResult(proc.stdout, model, True)


class _OpenCodeCLI(_SubprocessHarness):
    """Shared subprocess core for OpenCode-backed harnesses. The model id is
    whatever OpenCode is configured to accept (e.g. ``anthropic/claude-haiku-4-5``)."""

    name = "opencode"

    def __init__(
        self, *, binary: str = "opencode", timeout: float = 180.0, runner=_processes.run
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)


class OpenCodeHarness(_OpenCodeCLI):
    """Single-shot authoring via ``opencode run -m <model> <prompt>``."""

    name = "opencode"

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "-m", model, prompt]


class OpenCodeAgenticHarness(_OpenCodeCLI):
    """Stage-4 agentic browsing fallback via OpenCode's ``browse`` agent.

    Navigates the live site and emits the SAME ``SourceConfig`` envelope as
    single-shot (optionally a ``bespoke`` module). The page/token budget is an
    advisory directive embedded in the prompt; the subprocess ``timeout`` is the
    only hard ceiling."""

    name = "agentic"

    def __init__(
        self,
        *,
        binary: str = "opencode",
        page_budget: int = 8,
        token_budget: int = 200_000,
        timeout: float = 600.0,
        runner=_processes.run,
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        self._page_budget = page_budget
        self._token_budget = token_budget

    def _prepare(self, prompt: str) -> str:
        return _with_budget(prompt, self._page_budget, self._token_budget)

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "--agent", "browse", "-m", model, prompt]


class ClaudeCodeHarness(_SubprocessHarness):
    """Single-shot authoring via Claude Code's print mode:
    ``claude -p --model <model> [--effort <e>] --permission-mode bypassPermissions
    --output-format text <prompt>``.

    ``--permission-mode bypassPermissions`` enables the full toolset (notably
    WebFetch) so the agent can retrieve a candidate's live page when the embedded
    region is insufficient, instead of refusing. The subprocess runs in an isolated
    temp cwd (never the repo) so a stray file tool can't touch the project; the task
    only needs the prompt plus network. The ``model`` spec carries an optional
    ``@effort`` suffix (e.g. ``opus@low``); output is robustly parsed downstream
    (first balanced JSON object), so any tool preamble is tolerated."""

    name = "claude-code"

    def __init__(
        self,
        *,
        binary: str = "claude",
        timeout: float = 300.0,
        runner=_processes.run,
        cwd: str | None = None,
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        # Isolated scratch dir: keeps the tool-enabled agent out of the repo tree.
        self._cwd = cwd or tempfile.mkdtemp(prefix="jduk-author-")

    def _command(self, prompt: str, model: str) -> list[str]:
        model_id, effort = _split_effort(model)
        cmd = [self._binary, "-p", "--model", model_id]
        if effort:
            cmd += ["--effort", effort]
        cmd += ["--permission-mode", "bypassPermissions", "--output-format", "text", prompt]
        return cmd


class ClaudeCodeAgenticHarness(_SubprocessHarness):
    """Stage-4 agentic browsing fallback on Claude Code. Pre-approves the web tools
    (``--allowedTools WebFetch,WebSearch``) so the agent browses autonomously in
    non-interactive ``-p`` mode, and emits the SAME ``SourceConfig`` envelope as
    single-shot. The page/token budget is an advisory prompt directive; the
    subprocess ``timeout`` is the only hard ceiling.

    ``--allowedTools`` is a variadic flag, so it is placed before another flag
    (``--output-format``) — never immediately before the trailing positional
    prompt — to stop it swallowing the prompt as a tool name."""

    name = "agentic"

    def __init__(
        self,
        *,
        binary: str = "claude",
        page_budget: int = 8,
        token_budget: int = 200_000,
        timeout: float = 600.0,
        runner=_processes.run,
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        self._page_budget = page_budget
        self._token_budget = token_budget

    def _prepare(self, prompt: str) -> str:
        return _with_budget(prompt, self._page_budget, self._token_budget)

    def _command(self, prompt: str, model: str) -> list[str]:
        model_id, effort = _split_effort(model)
        cmd = [self._binary, "-p", "--model", model_id]
        if effort:
            cmd += ["--effort", effort]
        cmd += ["--allowedTools", "WebFetch,WebSearch", "--output-format", "text", prompt]
        return cmd


class _CommandCodeCLI(_SubprocessHarness):
    """Shared command builder for Command Code harnesses. Drives ``commandcode``
    in non-interactive print mode (``-p``) with the full automated-run flag set:
    ``--yolo`` bypasses every permission prompt (enabling the full toolset,
    including web fetch), ``--trust`` auto-trusts the project (skips the initial
    permission prompt), and ``--skip-onboarding`` skips taste onboarding. Command
    Code has no ``@effort`` concept, so the model spec (e.g.
    ``deepseek/deepseek-v4-flash``) is passed through verbatim. The prompt is the
    trailing positional — never placed right after ``-p``, whose optional
    ``[query]`` would otherwise swallow it."""

    name = "command-code"

    def __init__(
        self, *, binary: str = "commandcode", timeout: float = 600.0, runner=_processes.run
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)

    def _command(self, prompt: str, model: str) -> list[str]:
        return [
            self._binary,
            "-p",
            "--model",
            model,
            "--yolo",
            "--skip-onboarding",
            "--trust",
            prompt,
        ]


class CommandCodeHarness(_CommandCodeCLI):
    """Single-shot authoring via Command Code's print mode. Like the Claude Code
    harness, the subprocess runs in an isolated temp cwd (never the repo) so a
    tool call under ``--yolo`` can't touch the project tree; output is robustly
    parsed downstream (first balanced JSON object), so any tool preamble is
    tolerated."""

    name = "command-code"

    def __init__(
        self,
        *,
        binary: str = "commandcode",
        timeout: float = 600.0,
        runner=_processes.run,
        cwd: str | None = None,
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        # Isolated scratch dir: keeps the tool-enabled agent out of the repo tree.
        self._cwd = cwd or tempfile.mkdtemp(prefix="jduk-author-")


class CommandCodeAgenticHarness(_CommandCodeCLI):
    """Stage-4 agentic browsing fallback on Command Code. ``--yolo`` already
    enables the full toolset (including web fetch), so the agent browses
    autonomously and emits the SAME ``SourceConfig`` envelope as single-shot. The
    page/token budget is an advisory prompt directive; the subprocess ``timeout``
    is the only hard ceiling."""

    name = "agentic"

    def __init__(
        self,
        *,
        binary: str = "commandcode",
        page_budget: int = 8,
        token_budget: int = 200_000,
        timeout: float = 600.0,
        runner=_processes.run,
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        self._page_budget = page_budget
        self._token_budget = token_budget

    def _prepare(self, prompt: str) -> str:
        return _with_budget(prompt, self._page_budget, self._token_budget)
