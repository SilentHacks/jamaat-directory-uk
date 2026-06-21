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


class OpenCodeHarness:
    """Single-shot authoring via the OpenCode CLI in non-interactive mode.

    Shells out to ``opencode run -m <model> <prompt>`` and captures stdout.
    ``binary``/``timeout``/``runner`` are injectable so the call is unit-testable
    without spawning a real agent. The model id is whatever OpenCode is
    configured to accept (e.g. ``anthropic/claude-haiku-4-5``).
    """

    name = "opencode"

    def __init__(
        self, *, binary: str = "opencode", timeout: float = 180.0, runner=subprocess.run
    ) -> None:
        self._binary = binary
        self._timeout = timeout
        self._runner = runner

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "-m", model, prompt]

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        try:
            proc = self._runner(
                self._command(prompt, model),
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


HARNESSES: dict[str, type] = {"opencode": OpenCodeHarness}


def register_harness(name: str, cls: type) -> None:
    HARNESSES[name] = cls


def get_harness(name: str = "opencode", **kwargs) -> AuthorHarness:
    try:
        cls = HARNESSES[name]
    except KeyError:
        raise ValueError(f"unknown harness: {name!r} (have {sorted(HARNESSES)})") from None
    return cls(**kwargs)


class OpenCodeAgenticHarness:
    """Stage-4 agentic browsing fallback via the OpenCode CLI's browse agent.

    Navigates the live site under a per-site page/token budget and emits the SAME
    ``SourceConfig`` envelope as single-shot (optionally a ``bespoke`` module). The
    budget is embedded in the prompt (the agent is told to emit ``{}`` on overrun,
    which fails config validation downstream) and the subprocess ``timeout`` is the
    hard ceiling. ``binary``/``timeout``/``runner`` are injectable for testing.
    """

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
        self._binary = binary
        self._page_budget = page_budget
        self._token_budget = token_budget
        self._timeout = timeout
        self._runner = runner

    def _with_budget(self, prompt: str) -> str:
        # Page/token budget is a best-effort directive to the agent; the subprocess
        # timeout is the only hard ceiling for cost enforcement.
        return (
            f"{prompt}\n\nBudget: visit at most {self._page_budget} pages and "
            f"{self._token_budget} tokens. If you exceed either, stop and output "
            f"exactly {{}}."
        )

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "--agent", "browse", "-m", model, prompt]

    def run(self, prompt: str, *, model: str) -> HarnessResult:
        try:
            proc = self._runner(
                self._command(self._with_budget(prompt), model),
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


register_harness("agentic", OpenCodeAgenticHarness)
