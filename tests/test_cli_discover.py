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
