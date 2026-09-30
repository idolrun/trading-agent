"""Process settings from env / repo-root `.env`. Runtime trading config is in the DB (F006)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: PostgresDsn  # runtime role trading_app
    database_owner_url: PostgresDsn  # migrations / graph setup, role trading_owner
    db_pool_size: int = 10
    # Password auth (F009). The dashboard origin is the only origin allowed to change state.
    dashboard_origin: str = "http://localhost:3000"
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12  # covers one US session (19:15-01:45 local)
    step_up_window_s: int = 120
    auth_rate_limit_per_min: int = 10
    # Message API webhook (F011). HMAC-SHA256 over "<timestamp>.<raw body>".
    ingest_hmac_secret: SecretStr
    ingest_max_skew_s: int = 300
    # Background services (tests switch them off to drive them by hand).
    background_enabled: bool = True
    # Broker / market data adapters (F010). Only fakes until F051 (OD-3, OD-4).
    broker_adapter: Literal["fake"] = "fake"
    marketdata_adapter: Literal["fake"] = "fake"
    # Web Push (F029). Unset -> push disabled (SSE still works).
    vapid_private_key: SecretStr | None = None
    vapid_public_key: str | None = None
    vapid_subject: str | None = None  # "mailto:you@example.com"

    # Dev/demo/tests only: ParsedSignals per message id (F026). Ignored in production.
    interpretation_fixtures_dir: Path | None = None
    # LLM providers (F031). A missing key disables that provider at startup (TRD §4.8 rule 9).
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    openai_compat_api_key: SecretStr | None = None  # base_url goes in llm.params
    typesafe_api_key: SecretStr | None = None  # Jev fast path (F033); None = LLM path only
    # Discord account token for the live listener (selfbot). None = listener off. Never logged.
    discord_token: SecretStr | None = None
    # Development only: accept chain entries without a passing gate run (never in production).
    allow_ungated_models: bool = False
    # Integration tests only (tests/integration/conftest.py).
    test_database_url: PostgresDsn | None = None
    test_database_owner_url: PostgresDsn | None = None

    @property
    def ungated_models_allowed(self) -> bool:
        return self.allow_ungated_models and self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
