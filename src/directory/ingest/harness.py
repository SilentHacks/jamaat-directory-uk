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


class _OpenCodeCLI:
    """Shared subprocess core for OpenCode-backed harnesses.

    Subclasses supply the argv via ``_command`` and may massage the prompt via
    ``_prepare``. ``binary``/``timeout``/``runner`` are injectable so the call is
    unit-testable without spawning a real agent. The model id is whatever OpenCode
    is configured to accept (e.g. ``anthropic/claude-haiku-4-5``).
    """

    name = "opencode"

    def __init__(
        self, *, binary: str = "opencode", timeout: float = 180.0, runner=subprocess.run
    ) -> None:
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


class OpenCodeHarness(_OpenCodeCLI):
    """Single-shot authoring via ``opencode run -m <model> <prompt>``."""

    name = "opencode"

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "-m", model, prompt]


class OpenCodeAgenticHarness(_OpenCodeCLI):
    """Stage-4 agentic browsing fallback via OpenCode's ``browse`` agent.

    Navigates the live site and emits the SAME ``SourceConfig`` envelope as
    single-shot (optionally a ``bespoke`` module). The page/token budget is an
    advisory directive embedded in the prompt (the agent is told to emit ``{}`` on
    overrun, which fails config validation downstream); the subprocess ``timeout``
    is the only hard ceiling.
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
        super().__init__(binary=binary, timeout=timeout, runner=runner)
        self._page_budget = page_budget
        self._token_budget = token_budget

    def _prepare(self, prompt: str) -> str:
        return (
            f"{prompt}\n\nBudget: visit at most {self._page_budget} pages and "
            f"{self._token_budget} tokens. If you exceed either, stop and output "
            f"exactly {{}}."
        )

    def _command(self, prompt: str, model: str) -> list[str]:
        return [self._binary, "run", "--agent", "browse", "-m", model, prompt]
