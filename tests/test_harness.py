from types import SimpleNamespace

import pytest

from directory.ingest.harness import OpenCodeHarness, get_harness, register_harness


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


def test_get_harness_default_is_opencode():
    h = get_harness()
    assert h.name == "opencode"


def test_get_harness_unknown_raises():
    with pytest.raises(ValueError):
        get_harness("does-not-exist")


def test_register_harness_adds_a_client():
    class FakeCls:
        name = "fake"

        def run(self, prompt, *, model):  # pragma: no cover - not called here
            raise NotImplementedError

    register_harness("fake-task1", FakeCls)
    assert get_harness("fake-task1").name == "fake"


def test_agentic_harness_uses_browse_agent_and_embeds_budget():
    seen = {}

    def fake_runner(cmd, *, capture_output, text, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout='{"shape":"rules","rules":{"rules":[]}}',
                               stderr="")

    from directory.ingest.harness import OpenCodeAgenticHarness

    res = OpenCodeAgenticHarness(
        runner=fake_runner, page_budget=5, token_budget=1000, timeout=300.0
    ).run("find the timetable", model="strong/model")

    assert res.ok is True
    assert seen["cmd"][:5] == ["opencode", "run", "--agent", "browse", "-m"]
    assert seen["cmd"][5] == "strong/model"
    prompt_arg = seen["cmd"][6]
    assert "find the timetable" in prompt_arg
    assert "5 pages" in prompt_arg and "1000 tokens" in prompt_arg
    assert seen["timeout"] == 300.0


def test_agentic_harness_reports_failure_on_nonzero_exit():
    from directory.ingest.harness import OpenCodeAgenticHarness

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="navigation blew up")

    res = OpenCodeAgenticHarness(runner=fake_runner).run("p", model="m")
    assert res.ok is False
    assert res.error == "navigation blew up"


def test_agentic_harness_is_registered():
    assert get_harness("agentic").name == "agentic"
