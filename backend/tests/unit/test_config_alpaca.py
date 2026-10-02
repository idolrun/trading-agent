import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = {
    "database_url": "postgresql+psycopg://a:b@localhost/db",
    "database_owner_url": "postgresql+psycopg://a:b@localhost/db",
    "ingest_hmac_secret": "x",
}
KEYS = {"alpaca_api_key": "k", "alpaca_secret_key": "s"}
LIVE = "https://api.alpaca.markets/v2"


def settings(**kw: str) -> Settings:
    return Settings(_env_file=None, **BASE, **kw)  # type: ignore[arg-type]


def test_fakes_need_no_credentials() -> None:
    assert settings().broker_adapter == "fake"


def test_alpaca_needs_both_keys() -> None:
    with pytest.raises(ValidationError, match="ALPACA_API_KEY"):
        settings(broker_adapter="alpaca")
    with pytest.raises(ValidationError, match="ALPACA_SECRET_KEY"):
        settings(marketdata_adapter="alpaca", alpaca_api_key="k")
    assert settings(broker_adapter="alpaca", **KEYS).alpaca_endpoint.startswith("https://paper-")


def test_live_alpaca_only_in_a_production_environment() -> None:
    with pytest.raises(ValidationError, match="production"):
        settings(broker_adapter="alpaca", alpaca_endpoint=LIVE, **KEYS)
    settings(broker_adapter="alpaca", alpaca_endpoint=LIVE, environment="production", **KEYS)


def test_the_endpoint_is_read_from_alpacas_own_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(ValidationError, match="production"):
        settings(broker_adapter="alpaca", **KEYS)


def test_the_endpoint_must_be_an_alpaca_host() -> None:
    settings(broker_adapter="alpaca", alpaca_endpoint="https://paper-api.alpaca.markets/v2", **KEYS)
    with pytest.raises(ValidationError, match="APCA_API_BASE_URL"):
        settings(broker_adapter="alpaca", alpaca_endpoint="https://evil.example.com", **KEYS)
