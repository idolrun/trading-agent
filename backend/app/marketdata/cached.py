"""In-process cache in front of any `MarketData` (TRD §15.2: Redis-ready behind this accessor).

Only slow-changing reference data is cached: the optionable list (24 h), expiries (1 h) and
option chains (`chain_ttl`). Quotes are never cached here: a chain's quotes are only used for
the adjacent-strike warning, and every decision reads a fresh single-contract quote.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from datetime import date, timedelta
from decimal import Decimal

from app.instruments import Instrument, OptionContract
from app.marketdata.cache import TtlCache
from app.marketdata.port import MarketData, Quote
from app.markettime import Clock

logger = logging.getLogger(__name__)

Chain = list[tuple[OptionContract, Quote]]


class CachedMarketData:
    def __init__(
        self, inner: MarketData, clock: Clock, *, chain_ttl: timedelta = timedelta(seconds=45)
    ) -> None:
        self.inner = inner
        self._optionable: TtlCache[str, frozenset[str]] = TtlCache(timedelta(hours=24), clock)
        self._expiries: TtlCache[str, list[date]] = TtlCache(timedelta(hours=1), clock)
        self._chains: TtlCache[tuple[str, date], Chain] = TtlCache(chain_ttl, clock)
        self._loading: dict[tuple[str, date], asyncio.Task[Chain]] = {}

    async def quote(self, instrument: Instrument) -> Quote:
        return await self.inner.quote(instrument)

    async def underlying_price(self, symbol: str) -> Quote:
        return await self.inner.underlying_price(symbol)

    async def expiries(self, underlying: str) -> list[date]:
        return await self._expiries.get(underlying, lambda: self.inner.expiries(underlying))

    async def chain(self, underlying: str, expiry: date) -> Chain:
        key = (underlying, expiry)
        return await self._chains.get(key, lambda: self.inner.chain(underlying, expiry))

    async def chain_within(self, underlying: str, expiry: date, wait_s: float) -> Chain | None:
        """The chain if cached, or if it loads within `wait_s`; otherwise None while the load
        carries on in the background (one load per key). A caller on the critical path never
        waits for a cold full chain (1,000 rows a page, often seconds)."""
        key = (underlying, expiry)
        if (hit := self._chains.peek(key)) is not None:
            return hit
        task = self._loading.get(key)
        if task is None:
            task = asyncio.create_task(self.chain(underlying, expiry))
            self._loading[key] = task
            task.add_done_callback(lambda t: self._loaded(key, t))
        done, _ = await asyncio.wait({task}, timeout=wait_s)
        if task in done and not task.cancelled() and task.exception() is None:
            return task.result()
        return None

    def _loaded(self, key: tuple[str, date], task: asyncio.Task[Chain]) -> None:
        self._loading.pop(key, None)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.warning("chain load failed for %s %s: %s", *key, exc)

    async def optionable_symbols(self) -> frozenset[str]:
        return await self._optionable.get("all", self.inner.optionable_symbols)

    async def tick_size(self, instrument: Instrument, price: Decimal) -> Decimal:
        return await self.inner.tick_size(instrument, price)

    def stream(self, symbols: Iterable[str]) -> AsyncIterator[tuple[str, Quote]]:
        return self.inner.stream(symbols)


def cached(md: MarketData, clock: Clock) -> CachedMarketData:
    return md if isinstance(md, CachedMarketData) else CachedMarketData(md, clock)
