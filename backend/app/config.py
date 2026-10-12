"""Process settings from env / repo-root `.env`. Runtime trading config is in the DB (F006)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.broker.safety import ALPACA_LIVE, ALPACA_PAPER, FAKE

ALPACA_HOSTS = frozenset({"paper-api.alpaca.markets", "api.alpaca.markets"})
ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore", populate_by_name=True)

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: PostgresDsn  # runtime role trading_app
    database_owner_url: PostgresDsn  # migrations / graph setup, role trading_owner
    db_pool_size: int = 10
    # Password auth (F009). The dashboard origin is the only origin allowed to change state.
    dashboard_origin: str = "http://localhost:3000"
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12  # covers one US session (19:15-01:45 local)
    auth_rate_limit_per_min: int = 10
    # Message API webhook (F011). HMAC-SHA256 over "<timestamp>.<raw body>".
    ingest_hmac_secret: SecretStr
    ingest_max_skew_s: int = 300
    # Background services (tests switch them off to drive them by hand).
    background_enabled: bool = True
    # Broker / market data adapters (F010, F051). Alpaca: paper = paper trading, live = real money.
    broker_adapter: Literal["fake", "alpaca"] = "fake"
    marketdata_adapter: Literal["fake", "alpaca"] = "fake"
    # APCA_API_BASE_URL is Alpaca's own variable name; api.alpaca.markets = real money
    alpaca_endpoint: str = Field(
        "https://paper-api.alpaca.markets",
        validation_alias=AliasChoices("APCA_API_BASE_URL", "ALPACA_ENDPOINT"),
    )
    # APCA_* are Alpaca's own variable names (ALPACA_* still work); one key pair per process:
    # never paper and live together
    alpaca_api_key: str | None = Field(
        None, validation_alias=AliasChoices("APCA_API_KEY", "ALPACA_API_KEY")
    )
    alpaca_secret_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("APCA_SECRET_KEY", "ALPACA_SECRET_KEY")
    )
    alpaca_stock_feed: Literal["iex", "sip"] = "iex"  # iex is free; sip needs a paid plan
    alpaca_options_feed: Literal["indicative", "opra"] = "indicative"  # opra needs a paid plan
    alpaca_poll_interval_s: float = 5.0  # underlying-price polling (the order stream is pushed)
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
    # Checkpoint for the local Laya fallback (downloaded from Hugging Face on first use).
    laya_model: str = "aac6fef/laya-mlx"
    # Development only: accept chain entries without a passing gate run (never in production).
    allow_ungated_models: bool = False
    # Integration tests only (tests/integration/conftest.py).
    test_database_url: PostgresDsn | None = None
    test_database_owner_url: PostgresDsn | None = None

    @model_validator(mode="after")
    def _alpaca_settings(self) -> "Settings":
        if "alpaca" not in (self.broker_adapter, self.marketdata_adapter):
            return self
        if not self.alpaca_api_key or self.alpaca_secret_key is None:
            raise ValueError("APCA_API_KEY and APCA_SECRET_KEY are required")
        host = urlparse(self.alpaca_endpoint).netloc
        if host not in ALPACA_HOSTS:
            raise ValueError(f"APCA_API_BASE_URL must be one of {sorted(ALPACA_HOSTS)}")
        if host == "api.alpaca.markets" and self.environment != "production":
            raise ValueError("the live APCA_API_BASE_URL needs ENVIRONMENT=production")
        return self

    @property
    def broker_kind(self) -> str:
        """Which money orders would touch: `fake`, `alpaca-paper` or `alpaca-live`."""
        if self.broker_adapter != "alpaca":
            return FAKE
        live = urlparse(self.alpaca_endpoint).netloc == "api.alpaca.markets"
        return ALPACA_LIVE if live else ALPACA_PAPER

    @property
    def ungated_models_allowed(self) -> bool:
        return self.allow_ungated_models and self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
