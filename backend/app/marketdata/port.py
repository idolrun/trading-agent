"""The only interface to market data (TRD §2.2). Real adapter: F051 (OD-4)."""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from app.instruments import Instrument, OptionContract


class QuoteUnavailable(Exception):
    """No usable data for the instrument (OD-12: callers route to review, never guess)."""


@dataclass(frozen=True)
class Quote:
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    at: datetime


class MarketData(Protocol):
    async def quote(self, instrument: Instrument) -> Quote: ...

    async def underlying_price(self, symbol: str) -> Quote:
        """Includes index levels (SPX)."""
        ...

    async def expiries(self, underlying: str) -> list[date]: ...

    async def chain(self, underlying: str, expiry: date) -> list[tuple[OptionContract, Quote]]: ...

    async def optionable_symbols(self) -> frozenset[str]:
        """Refreshed daily (TRD §7.3, C86)."""
        ...

    async def tick_size(self, instrument: Instrument, price: Decimal) -> Decimal:
        """Minimum price increment at `price` (OD-25)."""
        ...

    def stream(self, symbols: Iterable[str]) -> AsyncIterator[tuple[str, Quote]]: ...
