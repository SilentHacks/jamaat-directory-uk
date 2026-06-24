from typer.testing import CliRunner

from directory import cli
from directory.ingest.author import AuthorOutcome
from directory.ingest.harness import OpenCodeAgenticHarness

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


def test_author_one_invokes_author_mosque(monkeypatch):
    monkeypatch.setattr(
        cli, "author_mosque",
        lambda engine, mid, **kwargs: AuthorOutcome(mid, "authored", "cheap"),
    )
    result = runner.invoke(cli.app, ["author", "--mosque-id", "m1"])
    assert result.exit_code == 0
    assert "m1: outcome=authored" in result.stdout


def test_author_agentic_flag_passes_fallback(monkeypatch, tmp_path):
    import directory.cli as cli

    captured = {}

    def fake_run_authoring(engine, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setenv("DIRECTORY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "run_authoring", fake_run_authoring)

    result = runner.invoke(cli.app, ["init-db"])
    assert result.exit_code == 0
    result = runner.invoke(cli.app, ["author", "--agentic"])
    assert result.exit_code == 0

    assert isinstance(captured["fallback"], OpenCodeAgenticHarness)
    assert captured["bespoke_root"] is not None


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
