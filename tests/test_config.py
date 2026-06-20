from pathlib import Path

from directory.config import Settings


def test_defaults():
    s = Settings()
    assert s.db_path == Path("data/directory.db")
    assert s.admin_api_key is None
    assert s.snapshot_horizon_days == 45


def test_database_url():
    s = Settings(db_path=Path("/tmp/x.db"))
    assert s.database_url == "sqlite:////tmp/x.db"


def test_env_override(monkeypatch):
    monkeypatch.setenv("DIRECTORY_ADMIN_API_KEY", "secret")
    s = Settings()
    assert s.admin_api_key == "secret"
