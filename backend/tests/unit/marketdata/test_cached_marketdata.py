"""F086: chains cached behind the MarketData port; a cold chain never blocks the caller."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.instruments import OptionContract
from app.marketdata.cached import CachedMarketData, cached
from app.marketdata.fake import FakeMarketData
from app.marketdata.port import MarketData, Quote
from app.markettime import FixedClock

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
DAY = date(2026, 10, 16)
CALL = OptionContract(underlying="SPY", expiry=DAY, strike=Decimal(600), right="call")


class Slow(FakeMarketData):
    def __init__(self, clock: FixedClock, delay_s: float) -> None:
        super().__init__(clock)
        self.delay_s = delay_s
        self.chain_calls = 0

    async def chain(self, underlying: str, expiry: date) -> list[tuple[OptionContract, Quote]]:
        self.chain_calls += 1
        await asyncio.sleep(self.delay_s)
        return await super().chain(underlying, expiry)


def setup(delay_s: float = 0) -> tuple[Slow, CachedMarketData, FixedClock]:
    clock = FixedClock(NOW)
    inner = Slow(clock, delay_s)
    inner.chains[("SPY", DAY)] = [CALL]
    inner.set_quote(CALL, Decimal("1.0"), Decimal("1.1"))
    return inner, CachedMarketData(inner, clock, chain_ttl=timedelta(seconds=45)), clock


def test_satisfies_port_and_wraps_once() -> None:
    _, md, clock = setup()
    port: MarketData = md
    assert cached(port, clock) is md


async def test_chain_is_cached_until_the_ttl() -> None:
    inner, md, clock = setup()
    await md.chain("SPY", DAY)
    await md.chain("SPY", DAY)
    assert inner.chain_calls == 1
    clock.advance(timedelta(seconds=46))
    await md.chain("SPY", DAY)
    assert inner.chain_calls == 2


async def test_a_quick_chain_is_returned_within_the_wait() -> None:
    _, md, _ = setup()
    chain = await md.chain_within("SPY", DAY, 0.3)
    assert chain is not None and chain[0][0] == CALL


async def test_a_slow_chain_returns_none_and_keeps_loading_once() -> None:
    inner, md, _ = setup(delay_s=0.2)
    assert await md.chain_within("SPY", DAY, 0.01) is None
    assert await md.chain_within("SPY", DAY, 0.01) is None  # same load, not a second one
    await asyncio.sleep(0.3)
    assert await md.chain_within("SPY", DAY, 0) is not None
    assert inner.chain_calls == 1


async def test_a_failed_load_is_none_and_retried_next_time() -> None:
    inner, md, _ = setup()
    inner.chains.clear()
    inner.quotes.clear()
    inner.chains[("SPY", DAY)] = [CALL]  # listed but unquoted: the fake raises
    assert await md.chain_within("SPY", DAY, 0.1) is None
    inner.set_quote(CALL, Decimal("1.0"), Decimal("1.1"))
    assert await md.chain_within("SPY", DAY, 0.1) is not None
