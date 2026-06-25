import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from directory.ingest.harness import (
    ClaudeCodeAgenticHarness,
    ClaudeCodeHarness,
    OpenCodeAgenticHarness,
    OpenCodeHarness,
    _Aborted,
    _processes,
    is_shutting_down,
    request_shutdown,
    reset_shutdown,
)


def test_opencode_returns_stdout_on_success():
    seen = {}

    def fake_runner(cmd, *, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout='{"shape":"rules"}', stderr="")

    res = OpenCodeHarness(runner=fake_runner, timeout=42.0).run("do it", model="cheap/model")

    assert res.ok is True
    assert res.text == '{"shape":"rules"}'
    assert res.model == "cheap/model"
    assert res.error is None
    assert seen["cmd"] == ["opencode", "run", "-m", "cheap/model", "do it"]
    assert seen["timeout"] == 42.0


def test_opencode_reports_nonzero_exit():
    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    res = OpenCodeHarness(runner=fake_runner).run("p", model="m")
    assert res.ok is False
    assert res.error == "boom"
    assert res.text == ""


def test_opencode_catches_subprocess_error():
    def fake_runner(cmd, **kwargs):
        raise OSError("opencode: not found")

    res = OpenCodeHarness(runner=fake_runner).run("p", model="m")
    assert res.ok is False
    assert "OSError" in res.error


def test_opencode_name():
    assert OpenCodeHarness().name == "opencode"


def test_agentic_harness_uses_browse_agent_and_embeds_budget():
    seen = {}

    def fake_runner(cmd, *, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout='{"shape":"rules","rules":{"rules":[]}}',
                               stderr="")

    res = OpenCodeAgenticHarness(
        runner=fake_runner, page_budget=5, token_budget=1000, timeout=300.0
    ).run("find the timetable", model="strong/model")

    assert res.ok is True
    assert OpenCodeAgenticHarness().name == "agentic"
    assert seen["cmd"][:5] == ["opencode", "run", "--agent", "browse", "-m"]
    assert seen["cmd"][5] == "strong/model"
    prompt_arg = seen["cmd"][6]
    assert "find the timetable" in prompt_arg
    assert "5 pages" in prompt_arg and "1000 tokens" in prompt_arg
    assert seen["timeout"] == 300.0


def test_agentic_harness_reports_failure_on_nonzero_exit():
    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="navigation blew up")

    res = OpenCodeAgenticHarness(runner=fake_runner).run("p", model="m")
    assert res.ok is False
    assert res.error == "navigation blew up"


def test_claude_code_builds_print_command_with_model_effort_and_tools():
    seen = {}

    def fake_runner(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"shape":"rules"}', stderr="")

    res = ClaudeCodeHarness(runner=fake_runner, timeout=120.0).run("do it", model="opus@low")

    assert res.ok is True
    assert res.text == '{"shape":"rules"}'
    assert res.model == "opus@low"
    cmd = seen["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert cmd[-1] == "do it"  # prompt is the trailing positional
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "low"
    # full toolset enabled so the agent can WebFetch the live page
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert seen["kwargs"]["timeout"] == 120.0
    # runs in an isolated temp dir, never the repo
    assert seen["kwargs"]["cwd"].startswith(tempfile.gettempdir())


def test_claude_code_omits_effort_when_unspecified():
    seen = {}

    def fake_runner(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    ClaudeCodeHarness(runner=fake_runner).run("p", model="opus")

    assert "--effort" not in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"


def test_claude_code_name_and_nonzero_exit():
    assert ClaudeCodeHarness().name == "claude-code"

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="kaboom")

    res = ClaudeCodeHarness(runner=fake_runner).run("p", model="opus@low")
    assert res.ok is False
    assert res.error == "kaboom"


def test_claude_code_agentic_allows_web_tools_and_embeds_budget():
    seen = {}

    def fake_runner(cmd, *, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout='{"shape":"rules","rules":{"rules":[]}}',
                               stderr="")

    res = ClaudeCodeAgenticHarness(
        runner=fake_runner, page_budget=5, token_budget=1000, timeout=300.0
    ).run("find the timetable", model="opus@low")

    assert res.ok is True
    assert ClaudeCodeAgenticHarness().name == "agentic"
    cmd = seen["cmd"]
    assert cmd[0] == "claude"
    assert cmd[-1].startswith("find the timetable")
    # web tools pre-approved so the agent browses autonomously in -p mode
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert "WebFetch" in allowed and "WebSearch" in allowed
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "low"
    prompt_arg = cmd[-1]
    assert "5 pages" in prompt_arg and "1000 tokens" in prompt_arg
    assert seen["timeout"] == 300.0


# --- process-group runner / Ctrl-C robustness -------------------------------

@pytest.fixture(autouse=True)
def _clear_shutdown():
    """Keep the module-singleton process manager clean between tests, even if a
    test latches shutdown."""
    reset_shutdown()
    yield
    reset_shutdown()


def test_managed_runner_returns_completed_process():
    proc = _processes.run(["printf", "hello"], capture_output=True, text=True, timeout=10)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode == 0
    assert proc.stdout == "hello"
    # the child is unregistered once it completes
    assert not _processes._live


def test_managed_runner_refuses_to_spawn_once_shutdown_latched():
    request_shutdown()
    assert is_shutting_down() is True
    with pytest.raises(_Aborted):
        _processes.run(["printf", "nope"], timeout=10)


def test_harness_returns_aborted_result_when_shutting_down():
    # default (managed) runner; latched shutdown means no real `claude` is spawned
    request_shutdown()
    res = ClaudeCodeHarness().run("p", model="opus@low")
    assert res.ok is False
    assert res.error == "aborted"


def test_shutdown_terminates_live_process_and_clears_registry():
    started = threading.Event()
    result = {}

    def _run_long():
        started.set()
        try:
            result["proc"] = _processes.run(["sleep", "30"], timeout=30)
        except BaseException as exc:  # pragma: no cover - defensive
            result["exc"] = exc

    t = threading.Thread(target=_run_long)
    t.start()
    started.wait(timeout=5)
    # wait until the child is registered as live
    deadline = time.monotonic() + 5
    while not _processes._live and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _processes._live, "long-running child was never registered"

    signalled = request_shutdown()
    assert signalled >= 1

    t.join(timeout=5)
    assert not t.is_alive(), "worker did not return after shutdown killed the child"
    assert not _processes._live  # registry drained
    # the killed process returns a non-zero / signal exit code, not a clean 0
    assert result.get("proc") is None or result["proc"].returncode != 0
