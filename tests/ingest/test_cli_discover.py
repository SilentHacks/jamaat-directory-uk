from typer.testing import CliRunner

from directory.cli import app

runner = CliRunner()


def test_discover_command_registered():
    result = runner.invoke(app, ["discover", "--help"])
    assert result.exit_code == 0
    assert "horizon-days" in result.output


def test_validate_websites_command_registered():
    result = runner.invoke(app, ["validate-websites", "--help"])
    assert result.exit_code == 0


def test_discover_exposes_concurrency_flag():
    result = runner.invoke(app, ["discover", "--help"])
    assert result.exit_code == 0
    assert "--concurrency" in result.output


def test_discover_exposes_force_flag():
    result = runner.invoke(app, ["discover", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.output


def test_validate_and_extract_expose_concurrency_flag():
    for cmd in ("validate-websites", "extract"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0
        assert "--concurrency" in result.output


def test_author_exposes_concurrency_flag():
    result = runner.invoke(app, ["author", "--help"])
    assert result.exit_code == 0
    assert "--concurrency" in result.output
