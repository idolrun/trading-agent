"""F019 with FakeMarketData fixtures (TRD §12 scenarios)."""

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.db import create_session_factory, in_transaction
from app.inbox.envelope import Envelope, EventKey
from app.inbox.models import EventType
from app.instruments import OptionContract
from app.interpret.signal import F, LiteralSpan, ParsedSignal, SignalFields
from app.ledger.models import ProviderPosition
from app.marketdata.fake import FakeMarketData, MarketFixture
from app.markettime import ET, FixedClock
from app.runtime_config.schema import RuntimeConfig
from app.validate.service import ValidationResult, ValidationService
from tests.integration.conftest import _url

FIXTURES = Path(__file__).parents[2] / "fixtures" / "market"
NOW = datetime(2026, 9, 28, 10, 4, tzinfo=ET)  # Monday
PROVIDER = 7
CFG = RuntimeConfig(
    identity_price_tolerance_pct=Decimal(25),
    adjacent_strike_ratio=Decimal("0.5"),
    missing_underlying_lookback_days=5,
)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def md() -> FakeMarketData:
    return FakeMarketData.from_fixtures(FIXTURES, FixedClock(NOW))


def service(factory: Factory, md: FakeMarketData) -> ValidationService:
    return ValidationService(
        factory, marketdata=md, alerts=AlertService(factory), clock=FixedClock(NOW)
    )


def lit(value: Any, raw: str | None = None) -> F[Any]:
    return F(value=value, source=LiteralSpan(raw=raw or str(value)))


def entry(
    underlying: str | None, strike: str, price: str, expiry: str = "0dte", right: str = "call"
) -> ParsedSignal:
    fields: dict[str, Any] = {
        "expiry_token": lit(expiry),
        "strike": lit(Decimal(strike)),
        "right": lit(right),
        "entry_price": lit(Decimal(price)),
    }
    if underlying:
        fields["underlying"] = lit(underlying)
    return ParsedSignal(msg_type="entry", instrument="option", fields=SignalFields(**fields))


ENV = Envelope(
    key=EventKey(1, EventType.CREATE, 0),
    provider_id=PROVIDER,
    channel_id=5,
    posted_at=NOW,
    received_at=NOW,
    text="x",
    payload={},
)


async def validate(
    factory: Factory, md: FakeMarketData, signal: ParsedSignal, config: RuntimeConfig = CFG
) -> ValidationResult:
    return await service(factory, md).validate(signal, ENV, config=config, now=NOW)


async def traded(factory: Factory, *underlyings: str) -> None:
    async with in_transaction(factory, None) as db:
        for u in underlyings:
            db.add(
                ProviderPosition(
                    provider_id=PROVIDER,
                    instrument="stock",
                    underlying=u,
                    status="closed",
                    opened_at=NOW - timedelta(days=1),
                )
            )


async def test_ac17_spx_0dte(factory: Factory, md: FakeMarketData) -> None:
    result = await validate(factory, md, entry("spx", "5800", "18.20"))
    assert result.status == "ok", result.reasons
    assert result.contract == OptionContract(
        underlying="SPXW", expiry=date(2026, 9, 28), strike=Decimal(5800), right="call"
    )


async def test_missing_underlying_suggests_by_proximity(
    factory: Factory, md: FakeMarketData
) -> None:
    md.set_underlying("MU", Decimal(995))
    await traded(factory, "MU", "TSLA")
    result = await validate(factory, md, entry(None, "1000", "5.00", expiry="fri"))
    assert (result.status, result.reasons, result.suggested_underlying) == (
        "review",
        ["underlying_missing"],
        "MU",
    )


async def test_two_nearby_underlyings_give_no_suggestion(
    factory: Factory, md: FakeMarketData
) -> None:
    md.set_underlying("MU", Decimal(995))
    md.set_underlying("AVGO", Decimal(1030))
    await traded(factory, "MU", "AVGO")
    result = await validate(factory, md, entry(None, "1000", "5.00", expiry="fri"))
    assert (result.status, result.suggested_underlying) == ("review", None)


async def test_no_whitelist_any_optionable_symbol_in_chain_is_ok(
    factory: Factory, md: FakeMarketData
) -> None:
    md.load(
        MarketFixture(
            underlying="ZZZ",
            price=Decimal(20),
            chains={date(2026, 10, 2): [(Decimal(20), "call", Decimal("1.00"), Decimal("1.10"))]},
        )
    )
    result = await validate(factory, md, entry("zzz", "20", "1.05", expiry="fri"))
    assert result.status == "ok", result.reasons


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        (entry("nope", "20", "1.00"), "not_optionable"),
        (entry("googl", "357.5", "2.00", expiry="10/9"), "contract_not_in_chain"),
        (entry("googl", "358", "2.00", expiry="fri"), "contract_not_in_chain"),
    ],
)
async def test_contract_must_exist(
    factory: Factory, md: FakeMarketData, signal: ParsedSignal, reason: str
) -> None:
    result = await validate(factory, md, signal)
    assert (result.status, result.reasons) == ("review", [reason])


async def test_price_far_off_quote_is_review(factory: Factory, md: FakeMarketData) -> None:
    chain = await md.chain("GOOGL", date(2026, 10, 2))
    mid = next(q for c, q in chain if c.strike == Decimal("357.5") and c.right == "call")
    far = (mid.bid + mid.ask) / 2 * 5  # type: ignore[operator]
    result = await validate(
        factory, md, entry("googl", "357.5", str(far.quantize(Decimal("0.01"))), expiry="fri")
    )
    assert (result.status, result.reasons) == ("review", ["price_off_quote"])


async def test_adjacent_strike_is_warning_only(factory: Factory, md: FakeMarketData) -> None:
    chain = await md.chain("GOOGL", date(2026, 10, 2))
    q360 = next(q for c, q in chain if c.strike == Decimal(360) and c.right == "call")
    price = ((q360.bid + q360.ask) / 2).quantize(Decimal("0.01"))  # type: ignore[operator]
    cfg = CFG.model_copy(update={"identity_price_tolerance_pct": Decimal(60)})
    result = await validate(factory, md, entry("googl", "357.5", str(price), expiry="fri"), cfg)
    assert result.status == "ok"
    assert result.warnings == ["adjacent_strike_better_match"]
    assert result.contract is not None and result.contract.strike == Decimal("357.5")  # type: ignore[union-attr]


async def test_market_data_failure_is_review_and_alert(
    factory: Factory, md: FakeMarketData, db_session: AsyncSession
) -> None:
    async def broken(*_: object) -> list[date]:
        raise ConnectionError("feed down")

    md.expiries = broken  # type: ignore[method-assign]
    result = await validate(factory, md, entry("googl", "357.5", "2.00", expiry="fri"))
    assert (result.status, result.reasons) == ("review", ["market_data_unavailable"])
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and (alert.kind, alert.severity) == ("market_data", "warning")


async def test_stock_entry_without_price_is_not_actionable(
    factory: Factory, md: FakeMarketData
) -> None:
    signal = ParsedSignal(
        msg_type="stock_entry", instrument="stock", fields=SignalFields(underlying=lit("tsla"))
    )
    result = await validate(factory, md, signal)
    assert (result.status, result.reasons) == ("not_actionable", ["no_entry_price"])


async def test_exits_skip_chain_checks(factory: Factory, md: FakeMarketData) -> None:
    trim = ParsedSignal(msg_type="trim", fields=SignalFields(exit_fraction="half"))
    result = await validate(factory, md, trim)
    assert result.status == "ok" and result.contract is None
