from typer.testing import CliRunner

from directory.cli import app
from tests.conftest import FIXTURES

runner = CliRunner()
FIXTURE = FIXTURES / "seed_sample.json"


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


def test_curate_applies_overlay(tmp_path, monkeypatch):
    import json

    db = tmp_path / "d.db"
    monkeypatch.setenv("DIRECTORY_DB_PATH", str(db))
    runner.invoke(app, ["seed", "--input", str(FIXTURE)])
    overlay = tmp_path / "dupes.json"
    overlay.write_text(json.dumps({
        "merge": [{"survivor": "masjid-e-umar-leicester", "drop": ["east-london-mosque"],
                   "reason": "dupe"}],
        "shared_url_review": [],
    }))
    result = runner.invoke(app, ["curate", "--input", str(overlay)])
    assert result.exit_code == 0
    assert "merged=1" in result.stdout


def test_import_mib_writes_seed_file(tmp_path):
    mib = FIXTURES / "mib_sample.json"
    out = tmp_path / "seed" / "mosques.json"
    result = runner.invoke(app, ["import-mib", "--input", str(mib), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "2" in result.stdout
