from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIRECTORY_", env_file=".env")

    db_path: Path = Path("data/directory.db")
    admin_api_key: str | None = None
    snapshot_horizon_days: int = 45

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
