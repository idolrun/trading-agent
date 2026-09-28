"""Process settings from env / repo-root `.env`. Runtime trading config is in the DB (F006)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: PostgresDsn  # runtime role trading_app
    database_owner_url: PostgresDsn  # migrations / graph setup, role trading_owner
    db_pool_size: int = 10
    # Integration tests only (tests/integration/conftest.py).
    test_database_url: PostgresDsn | None = None
    test_database_owner_url: PostgresDsn | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
