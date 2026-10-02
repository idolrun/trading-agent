"""Alpaca market-data adapter (F051, OD-4). Free tier: stocks from IEX (one exchange, thin quotes)
and options from the "indicative" feed (documented as delayed 15 min); the quote's `at` is
Alpaca's own timestamp, so staleness checks (FR-19) see the delay honestly.

Alpaca lists US equity and ETF options only: there is no index (SPX) quote and no SPXW chain, so
those signals end in review cards (QuoteUnavailable / not optionable).
`stream()` polls snapshots; the app's only streaming consumer is the underlying-stop watch.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.alpaca_api import AlpacaApi, AlpacaRefused, alpaca_symbol, dec, instrument_of
from app.instruments import Instrument, OptionContract, Stock
from app.marketdata.port import Quote, QuoteUnavailable
from app.markettime import ET, Clock, SystemClock

logger = logging.getLogger(__name__)

STOCK_TICK = Decimal("0.01")
# Penny-pilot classes tick finer, but a multiple of these is a valid price in every class.
# ponytail: per-root ticks (OD-25) when a pilot class needs prices below the coarse grid.
OPTION_TICKS = (Decimal("0.05"), Decimal("0.10"))  # below $3, at/above $3
PAGE = 1000


class AlpacaMarketData:
    def __init__(
        self,
        api: AlpacaApi,
        clock: Clock | None = None,
        *,
        stock_feed: str = "iex",
        options_feed: str = "indicative",
        poll_interval_s: float = 5.0,
    ) -> None:
        self._api = api
        self._clock = clock or SystemClock()
        self._stock_feed = stock_feed
        self._options_feed = options_feed
        self._poll_s = poll_interval_s

    async def quote(self, instrument: Instrument) -> Quote:
        return await self._one(alpaca_symbol(instrument), isinstance(instrument, OptionContract))

    async def underlying_price(self, symbol: str) -> Quote:
        return await self._one(symbol, option=False)

    async def expiries(self, underlying: str) -> list[date]:
        days: set[date] = set()
        token: str | None = None
        while True:
            body = await self._api.get(
                "/v2/options/contracts",
                underlying_symbols=underlying,
                status="active",
                expiration_date_gte=self._clock.now()
                .astimezone(ET)
                .date()
                .isoformat(),  # session date
                limit=10000,
                page_token=token,
            )
            days |= {date.fromisoformat(c["expiration_date"]) for c in body["option_contracts"]}
            if not (token := body.get("next_page_token")):
                return sorted(days)

    async def chain(self, underlying: str, expiry: date) -> list[tuple[OptionContract, Quote]]:
        chain: list[tuple[OptionContract, Quote]] = []
        token: str | None = None
        while True:
            body = await self._api.get(
                f"/v1beta1/options/snapshots/{underlying}",
                data=True,
                feed=self._options_feed,
                expiration_date=expiry.isoformat(),
                limit=PAGE,
                page_token=token,
            )
            for symbol, snapshot in body["snapshots"].items():
                try:
                    contract = instrument_of(symbol)
                except ValueError:  # an adjusted root (GOOGL1) is not a tradable root here
                    continue
                if isinstance(contract, OptionContract) and contract.underlying == underlying:
                    chain.append((contract, self._quote_of(snapshot)))
            if not (token := body.get("next_page_token")):
                return chain

    async def optionable_symbols(self) -> frozenset[str]:
        assets = await self._api.get("/v2/assets", status="active", attributes="options_enabled")
        return frozenset(a["symbol"] for a in assets)

    async def tick_size(self, instrument: Instrument, price: Decimal) -> Decimal:
        if isinstance(instrument, Stock):
            return STOCK_TICK
        return OPTION_TICKS[0] if price < 3 else OPTION_TICKS[1]

    async def stream(self, symbols: Iterable[str]) -> AsyncIterator[tuple[str, Quote]]:
        """Every poll yields every symbol, so a quiet market is not mistaken for a dead feed."""
        names = list(symbols)
        while True:
            try:
                snapshots = await self._snapshots(names, option=False)
                for name in names:
                    if name in snapshots:
                        yield name, self._quote_of(snapshots[name])
            except Exception as exc:  # a missed poll costs latency; the consumer alerts on a gap
                logger.warning("alpaca quote poll failed: %s", exc)
            await asyncio.sleep(self._poll_s)

    # --- internals ---

    async def _one(self, symbol: str, option: bool) -> Quote:
        try:
            snapshot = (await self._snapshots([symbol], option)).get(symbol)
        except AlpacaRefused as exc:  # an unknown symbol is a 400 on some endpoints
            raise QuoteUnavailable(f"{symbol}: {exc}") from exc
        quote = self._quote_of(snapshot) if snapshot else None
        if quote is None or all(v is None for v in (quote.bid, quote.ask, quote.last)):
            raise QuoteUnavailable(symbol)
        return quote

    async def _snapshots(self, symbols: list[str], option: bool) -> dict[str, dict[str, Any]]:
        try:
            return await self._snapshots_of(symbols, option)
        except AlpacaRefused:
            if len(symbols) == 1:
                raise
        # One symbol Alpaca refuses (an index like SPX) must not blind the others (the
        # underlying-stop watch reads several at once): ask one by one, keep what answers.
        found: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            try:
                found |= await self._snapshots_of([symbol], option)
            except AlpacaRefused as exc:
                logger.warning("alpaca refused %s: %s", symbol, exc)
        return found

    async def _snapshots_of(self, symbols: list[str], option: bool) -> dict[str, dict[str, Any]]:
        joined = ",".join(symbols)
        if option:
            body = await self._api.get(
                "/v1beta1/options/snapshots", data=True, symbols=joined, feed=self._options_feed
            )
            return dict(body.get("snapshots") or {})
        body = await self._api.get(
            "/v2/stocks/snapshots", data=True, symbols=joined, feed=self._stock_feed
        )
        return dict(body)

    def _quote_of(self, snapshot: dict[str, Any]) -> Quote:
        quote, trade = snapshot.get("latestQuote") or {}, snapshot.get("latestTrade") or {}
        stamps = [_time(x.get("t")) for x in (quote, trade)]
        at = max((s for s in stamps if s), default=self._clock.now())
        # A zero side is "no quote on that side" (IEX and indicative feeds), not a price of 0.
        return Quote(
            bid=dec(quote.get("bp")) or None,
            ask=dec(quote.get("ap")) or None,
            last=dec(trade.get("p")),
            at=at,
        )


def _time(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp).astimezone(UTC) if stamp else None
