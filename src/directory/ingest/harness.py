import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class HarnessResult:
    text: str
    model: str
    ok: bool
    error: str | None = None


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

    def __init__(self, *, binary: str, timeout: float = 180.0, runner=subprocess.run) -> None:
        self._binary = binary
        self._timeout = timeout
        self._runner = runner

    def _prepare(self, prompt: str) -> str:
        return prompt

    def _command(self, prompt: str, model: str) -> list[str]:
        raise NotImplementedError

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        try:
            proc = self._runner(
                self._command(self._prepare(prompt), model),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
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
        self, *, binary: str = "opencode", timeout: float = 180.0, runner=subprocess.run
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
        runner=subprocess.run,
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
    ``claude -p --model <model> [--effort <e>] --output-format text <prompt>``.

    The ``model`` spec carries an optional ``@effort`` suffix (e.g. ``opus@low``);
    a bare alias leaves the session's default effort. Output is robustly parsed
    downstream (first balanced JSON object), so any tool preamble is tolerated."""

    name = "claude-code"

    def __init__(
        self, *, binary: str = "claude", timeout: float = 180.0, runner=subprocess.run
    ) -> None:
        super().__init__(binary=binary, timeout=timeout, runner=runner)

    def _command(self, prompt: str, model: str) -> list[str]:
        model_id, effort = _split_effort(model)
        cmd = [self._binary, "-p", "--model", model_id]
        if effort:
            cmd += ["--effort", effort]
        cmd += ["--output-format", "text", prompt]
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
        runner=subprocess.run,
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
