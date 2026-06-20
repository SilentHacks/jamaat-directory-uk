from pathlib import Path

from typer.testing import CliRunner

from directory.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "seed_sample.json"


def test_init_db_creates_file(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(db))
    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0
    assert db.exists()


def test_seed_loads_records(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(db))
    result = runner.invoke(app, ["seed", "--input", str(FIXTURE)])
    assert result.exit_code == 0
    assert "2" in result.stdout


def test_import_mib_writes_seed_file(tmp_path):
    mib = Path(__file__).parent / "fixtures" / "mib_sample.json"
    out = tmp_path / "seed" / "mosques.json"
    result = runner.invoke(app, ["import-mib", "--input", str(mib), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "2" in result.stdout
