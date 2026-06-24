from types import SimpleNamespace

from directory.ingest.harness import (
    ClaudeCodeAgenticHarness,
    ClaudeCodeHarness,
    OpenCodeAgenticHarness,
    OpenCodeHarness,
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


def test_claude_code_builds_print_command_with_model_and_effort():
    seen = {}

    def fake_runner(cmd, *, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
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
    assert seen["timeout"] == 120.0


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
