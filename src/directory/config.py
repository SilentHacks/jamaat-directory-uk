from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIRECTORY_", env_file=".env")

    db_path: Path = Path("data/directory.db")
    admin_api_key: str | None = None
    snapshot_horizon_days: int = 45
    candidate_dir: Path = Path("data/candidates")
    author_harness: str = "opencode"
    author_model_cheap: str = "anthropic/claude-haiku-4-5"
    author_model_strong: str = "anthropic/claude-opus-4-8"
    author_max_calls: int = 50
    author_fallback_harness: str = "agentic"
    author_page_budget: int = 8
    author_token_budget: int = 200_000
    bespoke_dir: Path = Path("data/bespoke")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
