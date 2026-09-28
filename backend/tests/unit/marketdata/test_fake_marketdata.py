import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.instruments import OptionContract, Stock
from app.marketdata.cache import TtlCache
from app.marketdata.fake import FakeMarketData
from app.marketdata.port import MarketData, Quote, QuoteUnavailable
from app.markettime import FixedClock

FIXTURES = Path(__file__).parents[2] / "fixtures" / "market"
T0 = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


@pytest.fixture
def md() -> FakeMarketData:
    return FakeMarketData.from_fixtures(FIXTURES, FixedClock(T0))


def test_satisfies_port(md: FakeMarketData) -> None:
    port: MarketData = md
    assert port is md


async def test_fixtures_cover_the_listed_underlyings(md: FakeMarketData) -> None:
    assert await md.optionable_symbols() == {
        "SPY", "QQQ", "SPXW", "SPX", "TSLA", "GOOGL", "META", "MU",
    }  # fmt: skip


async def test_chain_and_quote(md: FakeMarketData) -> None:
    assert await md.expiries("GOOGL") == [date(2026, 10, 2), date(2026, 10, 16)]
    chain = await md.chain("GOOGL", date(2026, 10, 2))
    strikes = {c.strike for c, _ in chain}
    assert Decimal("357.5") in strikes
    contract = OptionContract(
        underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
    )
    quote = await md.quote(contract)
    assert quote.bid is not None and quote.ask is not None and quote.bid < quote.ask


async def test_spx_index_level_via_alias(md: FakeMarketData) -> None:
    assert (await md.underlying_price("SPX")).last == Decimal("5810.40")
    assert (await md.underlying_price("SPXW")).last == Decimal("5810.40")


async def test_unavailable_raises(md: FakeMarketData) -> None:
    with pytest.raises(QuoteUnavailable):
        await md.quote(Stock(symbol="NOPE"))
    with pytest.raises(QuoteUnavailable):
        await md.underlying_price("NOPE")
    assert await md.chain("SPY", date(2030, 1, 1)) == []


async def test_tick_sizes(md: FakeMarketData) -> None:
    spy = OptionContract(
        underlying="SPY", expiry=date(2026, 10, 2), strike=Decimal(580), right="call"
    )
    spx = OptionContract(
        underlying="SPXW", expiry=date(2026, 10, 2), strike=Decimal(5800), right="call"
    )
    assert await md.tick_size(spy, Decimal("2.10")) == Decimal("0.01")
    assert await md.tick_size(spy, Decimal("3.00")) == Decimal("0.05")
    assert await md.tick_size(spx, Decimal("2.10")) == Decimal("0.05")
    assert await md.tick_size(spx, Decimal("12")) == Decimal("0.10")
    assert await md.tick_size(Stock(symbol="TSLA"), Decimal("402.3")) == Decimal("0.01")


async def test_stream_delivers_subscribed_symbols_only(md: FakeMarketData) -> None:
    stream = md.stream(["SPX"])
    first = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)  # let the subscription register
    quote = Quote(bid=None, ask=None, last=Decimal(5790), at=T0)
    md.push("TSLA", quote)
    md.push("SPX", quote)
    assert await asyncio.wait_for(first, 1) == ("SPX", quote)
    await stream.aclose()


async def test_ttl_cache() -> None:
    clock = FixedClock(T0)
    cache: TtlCache[str, int] = TtlCache(timedelta(minutes=5), clock)
    loads = 0

    async def load() -> int:
        nonlocal loads
        loads += 1
        return loads

    assert await cache.get("chain", load) == 1
    assert await cache.get("chain", load) == 1  # cached
    clock.advance(timedelta(minutes=5))
    assert await cache.get("chain", load) == 2  # expired
    cache.invalidate("chain")
    assert await cache.get("chain", load) == 3
