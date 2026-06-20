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
