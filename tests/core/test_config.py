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


def test_candidate_dir_default():
    from directory.config import Settings

    assert str(Settings().candidate_dir) == "data/candidates"


def test_author_settings_have_defaults_and_env_overrides(monkeypatch):
    from directory.config import Settings, get_settings

    get_settings.cache_clear()
    s = Settings()
    assert s.author_model_cheap  # non-empty
    assert s.author_model_strong
    assert s.author_max_calls > 0

    monkeypatch.setenv("DIRECTORY_AUTHOR_MAX_CALLS", "3")
    get_settings.cache_clear()
    s2 = Settings()
    assert s2.author_max_calls == 3
    get_settings.cache_clear()


def test_author_budget_settings_defaults():
    from directory.config import Settings

    s = Settings()
    assert s.author_page_budget == 8
    assert s.author_token_budget == 200_000
    assert str(s.bespoke_dir) == "data/bespoke"
