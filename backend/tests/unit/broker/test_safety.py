"""Trading mode vs the broker this process talks to (paper money vs real money)."""

import pytest

from app.broker.safety import ALPACA_LIVE, ALPACA_PAPER, FAKE, check_mode, mode_validator
from app.config import Settings
from app.runtime_config.schema import RuntimeConfig, TradingMode


@pytest.mark.parametrize("kind", [FAKE, ALPACA_PAPER, ALPACA_LIVE])
def test_shadow_never_reaches_a_broker(kind: str) -> None:
    assert check_mode(TradingMode.SHADOW, kind) is None


def test_paper_needs_the_paper_endpoint() -> None:
    assert check_mode(TradingMode.PAPER, ALPACA_PAPER) is None
    assert "alpaca-paper" in (check_mode(TradingMode.PAPER, ALPACA_LIVE) or "")  # real money!
    assert check_mode(TradingMode.PAPER, FAKE) is not None  # not a paper account either


def test_live_needs_the_live_endpoint() -> None:
    assert check_mode(TradingMode.LIVE, ALPACA_LIVE) is None
    assert check_mode(TradingMode.LIVE, ALPACA_PAPER) is not None
    assert check_mode(TradingMode.LIVE, FAKE) is not None


def test_the_fake_broker_serves_paper_and_live_only_in_tests() -> None:
    assert check_mode(TradingMode.PAPER, FAKE, allow_fake=True) is None
    assert check_mode(TradingMode.LIVE, FAKE, allow_fake=True) is None
    assert check_mode(TradingMode.PAPER, ALPACA_LIVE, allow_fake=True) is not None  # still no


def test_config_validator_reports_the_mismatch() -> None:
    validate = mode_validator(ALPACA_LIVE)
    assert validate(RuntimeConfig(trading_mode=TradingMode.SHADOW)) == []
    [error] = validate(RuntimeConfig(trading_mode=TradingMode.PAPER))
    assert "trading_mode=paper" in error


def test_settings_name_the_broker_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Settings.model_construct(
        broker_adapter="fake", alpaca_endpoint="https://paper-api.alpaca.markets"
    )
    assert base.broker_kind == FAKE
    paper = base.model_copy(update={"broker_adapter": "alpaca"})
    assert paper.broker_kind == ALPACA_PAPER
    live = paper.model_copy(update={"alpaca_endpoint": "https://api.alpaca.markets"})
    assert live.broker_kind == ALPACA_LIVE
