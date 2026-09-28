"""Deterministic in-memory market data for tests, shadow runs and dev (until F051)."""

import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.instruments import Instrument, OptionContract, Stock, broker_symbol
from app.marketdata.port import Quote, QuoteUnavailable
from app.markettime import Clock, SystemClock

STOCK_TICK = Decimal("0.01")
DEFAULT_OPTION_TICKS = (Decimal("0.05"), Decimal("0.10"))  # below $3, at/above $3


class MarketFixture(BaseModel):
    """`tests/fixtures/market/<ROOT>.json`: synthetic data only (never docs/data, C87)."""

    underlying: str
    aliases: list[str] = []  # e.g. SPX index level for the SPXW root
    price: Decimal
    ticks: tuple[Decimal, Decimal] | None = None  # (below $3, at/above $3)
    chains: dict[date, list[tuple[Decimal, Literal["call", "put"], Decimal, Decimal]]] = {}


class FakeMarketData:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.quotes: dict[str, Quote] = {}  # by broker symbol
        self.underlyings: dict[str, Quote] = {}
        self.chains: dict[tuple[str, date], list[OptionContract]] = {}
        self.optionable: set[str] = set()
        self.option_ticks: dict[str, tuple[Decimal, Decimal]] = {}
        self._subscribers: list[tuple[frozenset[str], asyncio.Queue[tuple[str, Quote]]]] = []

    @classmethod
    def from_fixtures(cls, directory: Path, clock: Clock | None = None) -> "FakeMarketData":
        md = cls(clock)
        for path in sorted(directory.glob("*.json")):
            md.load(MarketFixture.model_validate_json(path.read_text()))
        return md

    def load(self, fixture: "MarketFixture") -> None:
        root = fixture.underlying
        for name in (root, *fixture.aliases):
            self.set_underlying(name, fixture.price)
            self.optionable.add(name)
        if fixture.ticks is not None:
            self.option_ticks[root] = fixture.ticks
        for expiry, rows in fixture.chains.items():
            contracts = self.chains.setdefault((root, expiry), [])
            for strike, right, bid, ask in rows:
                contract = OptionContract(
                    underlying=root, expiry=expiry, strike=strike, right=right
                )
                contracts.append(contract)
                self.set_quote(contract, bid, ask)

    # --- scripting ---

    def set_quote(
        self,
        instrument: Instrument,
        bid: Decimal | None,
        ask: Decimal | None,
        last: Decimal | None = None,
    ) -> Quote:
        quote = Quote(bid=bid, ask=ask, last=last, at=self.clock.now())
        self.quotes[broker_symbol(instrument)] = quote
        return quote

    def set_underlying(self, symbol: str, price: Decimal) -> Quote:
        quote = Quote(bid=price, ask=price, last=price, at=self.clock.now())
        self.underlyings[symbol] = quote
        return quote

    def push(self, symbol: str, quote: Quote) -> None:
        """Deliver a streamed quote to every `stream()` subscribed to `symbol`."""
        for symbols, queue in self._subscribers:
            if symbol in symbols:
                queue.put_nowait((symbol, quote))

    # --- MarketData ---

    async def quote(self, instrument: Instrument) -> Quote:
        try:
            return self.quotes[broker_symbol(instrument)]
        except KeyError:
            raise QuoteUnavailable(broker_symbol(instrument)) from None

    async def underlying_price(self, symbol: str) -> Quote:
        try:
            return self.underlyings[symbol]
        except KeyError:
            raise QuoteUnavailable(symbol) from None

    async def expiries(self, underlying: str) -> list[date]:
        return sorted(expiry for root, expiry in self.chains if root == underlying)

    async def chain(self, underlying: str, expiry: date) -> list[tuple[OptionContract, Quote]]:
        return [(c, await self.quote(c)) for c in self.chains.get((underlying, expiry), [])]

    async def optionable_symbols(self) -> frozenset[str]:
        return frozenset(self.optionable)

    async def tick_size(self, instrument: Instrument, price: Decimal) -> Decimal:
        if isinstance(instrument, Stock):
            return STOCK_TICK
        below, above = self.option_ticks.get(instrument.underlying, DEFAULT_OPTION_TICKS)
        return below if price < 3 else above

    async def stream(self, symbols: Iterable[str]) -> AsyncIterator[tuple[str, Quote]]:
        queue: asyncio.Queue[tuple[str, Quote]] = asyncio.Queue()
        entry = (frozenset(symbols), queue)
        self._subscribers.append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(entry)
