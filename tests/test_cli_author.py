from typer.testing import CliRunner

from directory import cli
from directory.ingest.author import AuthorOutcome

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
