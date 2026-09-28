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
    # Passkey auth (F009). The dashboard origin is also the WebAuthn origin; rp_id its host.
    rp_id: str = "localhost"
    rp_name: str = "Trading Agent"
    dashboard_origin: str = "http://localhost:3000"
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12  # covers one US session (19:15-01:45 local)
    step_up_window_s: int = 120
    auth_rate_limit_per_min: int = 10
    # Broker / market data adapters (F010). Only fakes until F051 (OD-3, OD-4).
    broker_adapter: Literal["fake"] = "fake"
    marketdata_adapter: Literal["fake"] = "fake"
    # Integration tests only (tests/integration/conftest.py).
    test_database_url: PostgresDsn | None = None
    test_database_owner_url: PostgresDsn | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
