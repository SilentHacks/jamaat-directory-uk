from typer.testing import CliRunner

from directory import cli
from directory.ingest.author import AuthorOutcome
from directory.ingest.harness import (
    ClaudeCodeAgenticHarness,
    ClaudeCodeHarness,
    OpenCodeAgenticHarness,
    OpenCodeHarness,
)

runner = CliRunner()


def test_author_all_invokes_run_authoring(monkeypatch):
    seen = {}

    def fake_run(engine, **kwargs):
        seen.update(kwargs)
        return [AuthorOutcome("m1", "authored", "cheap"), AuthorOutcome("m2", "review", "strong")]

    monkeypatch.setattr(cli, "run_authoring", fake_run)
    result = runner.invoke(cli.app, ["author", "--max-calls", "7"])

    assert result.exit_code == 0
    assert "Authored 2 mosque(s)" in result.stdout
    assert seen["max_calls"] == 7
    assert seen["models"][0]  # cheap model wired from settings


def test_author_ctrl_c_exits_cleanly_with_summary(monkeypatch, tmp_path):
    import directory.cli as cli

    def fake_run(engine, **kwargs):
        # simulate one completed mosque, then an operator Ctrl-C mid-batch
        kwargs["on_outcome"](1, 5, AuthorOutcome("m1", "authored", "opus@low"))
        raise KeyboardInterrupt

    shutdown_calls = []
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", fake_run)
    monkeypatch.setattr(cli, "request_shutdown", lambda: shutdown_calls.append(True) or 0)

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["author"])

    assert result.exit_code == 130  # standard interrupted-by-Ctrl-C code
    assert "Interrupted after 1 mosque(s)" in result.output
    assert "authored=1" in result.output
    assert shutdown_calls  # agents were told to terminate


def test_author_one_invokes_author_mosque(monkeypatch):
    monkeypatch.setattr(
        cli, "author_mosque",
        lambda engine, mid, **kwargs: AuthorOutcome(mid, "authored", "cheap"),
    )
    result = runner.invoke(cli.app, ["author", "--mosque-id", "m1"])
    assert result.exit_code == 0
    assert "m1: outcome=authored" in result.stdout


def test_author_defaults_to_claude_code_opus_low(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}

    def fake_run_authoring(engine, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", fake_run_authoring)

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["author"])
    assert result.exit_code == 0

    assert isinstance(captured["harness"], ClaudeCodeHarness)
    assert captured["models"] == ("opus@low",)  # single low-effort model by default
    assert captured["fallback"] is None  # no agentic, no high-effort fallback


def test_author_fallback_flag_appends_high_effort(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", lambda engine, **kw: captured.update(kw) or [])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["author", "--fallback"])
    assert result.exit_code == 0
    assert captured["models"] == ("opus@low", "opus@high")


def test_author_agentic_uses_claude_code_browse_at_low_effort(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", lambda engine, **kw: captured.update(kw) or [])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["author", "--agentic"])
    assert result.exit_code == 0
    assert isinstance(captured["fallback"], ClaudeCodeAgenticHarness)
    assert captured["fallback_model"] == "opus@low"
    assert captured["bespoke_root"] is not None


def test_author_opencode_harness_uses_legacy_ladder(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", lambda engine, **kw: captured.update(kw) or [])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["author", "--harness", "opencode", "--agentic"])
    assert result.exit_code == 0
    assert isinstance(captured["harness"], OpenCodeHarness)
    assert isinstance(captured["fallback"], OpenCodeAgenticHarness)
    assert len(captured["models"]) == 2  # cheap → strong ladder


def test_reauthor_no_verify_only_invokes_model_path(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_bespoke", lambda root: [])
    monkeypatch.setattr(cli, "run_reauthor", lambda engine, **kw: captured.update(kw) or
                        [AuthorOutcome("m1", "authored", "opus@low")])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["reauthor", "--no-verify-only"])
    assert result.exit_code == 0
    assert isinstance(captured["harness"], ClaudeCodeHarness)
    assert captured["models"] == ("opus@low",)
    assert "recovered 1" in result.stdout


def test_reauthor_invokes_verify_retry(monkeypatch, tmp_path):
    import directory.cli as cli
    from directory.ingest.runner import ExtractOutcome

    seen = {}

    def fake_verify(engine, **kwargs):
        seen.update(kwargs)
        return [ExtractOutcome("m1", True, 5, "auto_accept", "authored"),
                ExtractOutcome("m2", False, 0, "auto_reject", "needs_reauthor")]

    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_verify_retry", fake_verify)
    monkeypatch.setattr(cli, "load_bespoke", lambda root: [])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["reauthor"])

    assert result.exit_code == 0
    assert "recovered 1" in result.stdout
    assert "m1: status=authored" in result.stdout


def test_extract_loads_bespoke_modules(monkeypatch, tmp_path):
    import directory.cli as cli

    seen = {}

    def fake_load_bespoke(root):
        seen["root"] = root
        return []

    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_bespoke", fake_load_bespoke)
    monkeypatch.setattr(cli, "run_extract", lambda engine, **kwargs: [])

    runner.invoke(cli.app, ["init-db"])
    result = runner.invoke(cli.app, ["extract"])

    assert result.exit_code == 0
    assert str(seen["root"]).endswith("bespoke")
