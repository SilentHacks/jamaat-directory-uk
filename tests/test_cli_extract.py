from unittest.mock import patch

from typer.testing import CliRunner

from directory.cli import app
from directory.ingest.runner import ExtractOutcome

runner = CliRunner()


def test_extract_all_invokes_run_extract():
    fake = [ExtractOutcome("s1", True, 5, "auto_accept", "authored")]
    with patch("directory.cli.run_extract", return_value=fake) as m:
        result = runner.invoke(app, ["extract", "--horizon-days", "7"])
    assert result.exit_code == 0
    assert "s1" in result.stdout
    assert m.call_args.kwargs["horizon_days"] == 7


def test_extract_single_source():
    out = ExtractOutcome("s1", True, 5, "auto_accept", "authored")
    with patch("directory.cli.extract_source", return_value=out) as m:
        result = runner.invoke(app, ["extract", "--source-id", "s1"])
    assert result.exit_code == 0
    assert m.call_args.args[1] == "s1"
