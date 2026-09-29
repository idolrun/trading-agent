"""System-held underlying stops (F044, PRD 7.4, C49, §9, TRD §7.3).

A stop defined on the underlying ("769 stop" on SPY 770C) is a `stops` row held by the system.
This lifespan task watches the underlyings of the working ones and, when crossed, claims the
row exactly once and fires the C25 exit (`Auto(underlying_stop)`, pre-approved by the card).
Trigger (OD-32, confirmed 2026-09-29): the last trade; calls and long stock fire at
`last <= level`, puts at `last >= level`. Candle-close conditions are information only.
While the process is down nothing protects these stops (TRD §9 known gap).
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.execution.exit import ExitPolicy
from app.execution.intent import Auto
from app.execution.models import AutoReason, HeldBy, Stop, StopChange, StopEvent, StopStatus
from app.ledger.models import Lot, OptionRight
from app.marketdata.port import MarketData, Quote

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Watched:
    stop_id: uuid.UUID
    lot_id: uuid.UUID
    underlying: str
    level: Decimal
    fires_on_rise: bool  # puts


def crossed(w: Watched, last: Decimal) -> bool:
    return last >= w.level if w.fires_on_rise else last <= w.level


class UnderlyingStopWatcher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        marketdata: MarketData,
        exits: ExitPolicy,
        alerts: AlertService,
        gap_s: float = 10.0,
        reload_s: float = 5.0,
    ) -> None:
        self._sessions = sessions
        self._md = marketdata
        self._exits = exits
        self._alerts = alerts
        self._gap_s = gap_s
        # ponytail: re-reads the working stops every reload_s; NOTIFY-driven if this ever lags
        self._reload_s = reload_s
        self._silent = False  # one alert per silent episode

    async def load(self) -> list[Watched]:
        async with self._sessions() as db:
            rows = await db.execute(
                select(Stop, Lot)
                .join(Lot, col(Lot.lot_id) == col(Stop.lot_id))
                .where(col(Stop.held_by) == HeldBy.SYSTEM, col(Stop.status) == StopStatus.WORKING)
            )
            return [
                Watched(
                    stop.stop_id,
                    lot.lot_id,
                    lot.underlying,
                    stop.level,
                    lot.option_right == OptionRight.PUT,
                )
                for stop, lot in rows.tuples().all()
            ]

    async def on_price(self, symbol: str, last: Decimal, watched: list[Watched]) -> list[uuid.UUID]:
        """Fire every crossed stop on `symbol`; returns the stops this call fired."""
        fired = []
        for w in watched:
            if w.underlying == symbol and crossed(w, last) and await self._claim(w):
                fired.append(w.stop_id)
                await self._fire(w, last)
        return fired

    async def run(self) -> None:
        while True:
            watched = await self.load()
            if not watched:
                await asyncio.sleep(self._reload_s)
                continue
            await self._watch(watched)

    async def _watch(self, watched: list[Watched]) -> None:
        """One subscription for `reload_s`; a silent stream longer than `gap_s` alerts."""
        symbols = sorted({w.underlying for w in watched})
        queue: asyncio.Queue[tuple[str, Quote]] = asyncio.Queue()

        async def pump() -> None:  # a timeout must never cancel the stream itself
            async for item in self._md.stream(symbols):
                queue.put_nowait(item)

        pumping = asyncio.create_task(pump(), name="underlying-stream")
        loop = asyncio.get_running_loop()
        until = loop.time() + self._reload_s
        try:
            while (left := until - loop.time()) > 0:
                try:
                    symbol, quote = await asyncio.wait_for(queue.get(), min(left, self._gap_s))
                except TimeoutError:
                    if loop.time() < until:
                        await self._gap(symbols)
                    continue
                self._silent = False
                if quote.last is not None:
                    fired = await self.on_price(symbol, quote.last, watched)
                    watched = [w for w in watched if w.stop_id not in fired]
        finally:
            pumping.cancel()
            await asyncio.gather(pumping, return_exceptions=True)

    async def _claim(self, w: Watched) -> bool:
        """Exactly once: only the transition working -> fired returns a row."""
        async with in_transaction(self._sessions, None) as db:
            claimed = await db.scalar(
                update(Stop)
                .where(col(Stop.stop_id) == w.stop_id, col(Stop.status) == StopStatus.WORKING)
                .values(status=StopStatus.FIRED)
                .returning(col(Stop.stop_id))
            )
            if claimed is not None:
                db.add(StopEvent(stop_id=w.stop_id, change=StopChange.FIRE))
        return claimed is not None

    async def _fire(self, w: Watched, last: Decimal) -> None:
        async with self._sessions() as db:
            lot = await db.get(Lot, w.lot_id)
        qty = lot.qty if lot is not None else 0
        await self._alerts.raise_(
            AlertKind.UNDERLYING_STOP,
            Severity.URGENT,
            f"{w.underlying} traded {last} through the stop {w.level}: exiting {qty}",
            details={"level": str(w.level), "last": str(last), "qty": qty},
            dedupe_key=f"underlying_stop:{w.stop_id}",
            lot_id=w.lot_id,
        )
        if qty > 0:
            try:
                await self._exits.execute(
                    w.lot_id, qty, Auto(AutoReason.UNDERLYING_STOP, ref=str(w.stop_id))
                )
            except Exception:
                logger.exception("underlying stop exit failed", extra={"lot_id": str(w.lot_id)})

    async def _gap(self, symbols: list[str]) -> None:
        if self._silent:
            return
        self._silent = True
        await self._alerts.raise_(
            AlertKind.MARKET_DATA,
            Severity.WARNING,
            f"No underlying prices for {self._gap_s:.0f}s: underlying stops unprotected",
            details={"symbols": symbols},
            dedupe_key=None,
        )
