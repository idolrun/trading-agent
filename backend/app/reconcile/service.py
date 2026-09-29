"""Reconciliation (F046, PRD §11, C46, C47): the broker is the source of truth.

`compare` is pure: broker positions/orders against the open lots and known client order ids.
`Reconciler.run` applies the result: orders are synced from the broker first (fills go through
the F040 consumer), foreign lots are created/adjusted (C47), and any mismatch or unknown `cta-`
order raises an alert and blocks entries until a later clean run (or a manual fix) resolves it.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.broker.port import BrokerClient, BrokerEvent, BrokerOrder, BrokerPosition
from app.db import in_transaction
from app.execution import repo as orders_repo
from app.execution.consumer import BrokerEventConsumer
from app.execution.fills import Applied
from app.execution.intent import CLIENT_ID_PREFIX
from app.execution.models import Order, OrderEventSource, OrderStatus
from app.instruments import Instrument as Contract
from app.instruments import broker_symbol
from app.ledger import repo as ledger
from app.ledger.lots import LotUpdate
from app.ledger.models import Lot, LotChange
from app.markettime import Clock, is_regular_hours
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig, TradingMode
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName

logger = logging.getLogger(__name__)
FILL_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED})
# Only a block this service (or startup) set may be lifted by a clean run.
SELF_CLEARED_REASONS = ("startup", "reconciliation")
AFTER_FILL_DEBOUNCE_S = 2.0
# Orders touched this recently are left to the event stream (a synthesized fill would be
# applied twice when the real one arrives). Startup runs with 0: no stream is running yet.
PERIODIC_SETTLE_S = 30.0


@dataclass(frozen=True)
class Mismatch:
    symbol: str
    broker_qty: int
    lot_qty: int


@dataclass(frozen=True)
class ForeignChange:
    """Bring the foreign lot of a symbol to the broker's quantity (C47)."""

    qty: int
    avg: Decimal | None
    lot_id: uuid.UUID | None = None  # existing foreign lot, None = create
    instrument: Contract | None = None  # needed to create


@dataclass(frozen=True)
class ReconcileReport:
    at: datetime | None = None
    mismatches: tuple[Mismatch, ...] = ()
    foreign: tuple[ForeignChange, ...] = ()
    unknown_orders: tuple[str, ...] = ()  # our `cta-` client ids the DB has no row for
    orders_synced: int = 0
    skipped: str | None = None  # e.g. "shadow"; a skipped run counts as clean
    error: str | None = None  # broker unreadable: cannot verify -> not clean

    @property
    def clean(self) -> bool:
        return not (self.mismatches or self.unknown_orders or self.error)


def compare(
    positions: Sequence[BrokerPosition],
    lots: Sequence[Lot],
    broker_orders: Sequence[BrokerOrder],
    known_client_ids: set[str],
) -> ReconcileReport:
    """Per broker symbol: broker qty vs Σ open lot qty (tracked + foreign). Without tracked
    lots the broker's holding is simply adopted as a foreign lot; with them, any difference
    (also a lot the broker no longer holds) is a mismatch."""
    at_broker = {broker_symbol(p.instrument): p for p in positions}
    held: dict[str, list[Lot]] = defaultdict(list)
    for lot in lots:
        held[lot.broker_symbol].append(lot)
    mismatches, foreign = [], []
    for symbol in sorted(at_broker.keys() | held.keys()):
        position, symbol_lots = at_broker.get(symbol), held.get(symbol, [])
        broker_qty = position.qty if position else 0
        lot_qty = sum(lot.qty for lot in symbol_lots)
        if broker_qty == lot_qty:
            continue
        if broker_qty < 0 or any(not lot.is_foreign for lot in symbol_lots):
            mismatches.append(Mismatch(symbol, broker_qty, lot_qty))
            continue
        existing = symbol_lots[0] if symbol_lots else None
        rest = lot_qty - (existing.qty if existing else 0)
        avg = position.avg_price if position else None
        foreign.append(
            ForeignChange(
                qty=max(broker_qty - rest, 0),
                avg=avg if avg is not None or existing is None else existing.avg_price,
                lot_id=existing.lot_id if existing else None,
                instrument=position.instrument if position else None,
            )
        )
    unknown = tuple(
        o.client_order_id
        for o in broker_orders
        if o.client_order_id.startswith(CLIENT_ID_PREFIX)
        and o.client_order_id not in known_client_ids
    )
    return ReconcileReport(
        mismatches=tuple(mismatches), foreign=tuple(foreign), unknown_orders=unknown
    )


def order_events(
    order: Order, applied_qty: int, found: BrokerOrder, at: datetime
) -> list[BrokerEvent]:
    """Events that bring a DB order to the broker's view; F040 applies them. The fill id is
    derived from the broker's cumulative quantity, so a repeat run adds nothing."""
    coid = order.client_order_id
    events = []
    delta = found.filled_qty - applied_qty
    if delta > 0:
        events.append(
            BrokerEvent(
                kind="fill",
                broker_order_id=found.broker_order_id,
                client_order_id=coid,
                status=found.status,
                at=at,
                fill_id=f"reconcile:{coid}:{found.filled_qty}",
                qty=delta,
                price=_fill_price(applied_qty, order.avg_fill_price, found, delta),
            )
        )
    if found.status != order.status and not (delta > 0 and found.status in FILL_STATUSES):
        events.append(
            BrokerEvent(
                kind="status",
                broker_order_id=found.broker_order_id,
                client_order_id=coid,
                status=found.status,
                at=at,
            )
        )
    return events


def _fill_price(
    applied_qty: int, applied_avg: Decimal | None, found: BrokerOrder, delta: int
) -> Decimal | None:
    """Price of the missing quantity from the two cumulative averages (fallback: the broker's)."""
    if found.avg_fill_price is None:
        return None
    if applied_qty == 0 or applied_avg is None:
        return found.avg_fill_price
    price = (found.avg_fill_price * found.filled_qty - applied_avg * applied_qty) / delta
    return price.quantize(Decimal("0.0001")) if price > 0 else found.avg_fill_price


def _findings(report: ReconcileReport) -> dict[str, tuple[str, dict[str, Any]]]:
    """signature -> (alert message, details) for everything that keeps entries blocked."""
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in report.mismatches:
        found[f"lot:{m.symbol}:{m.broker_qty}:{m.lot_qty}"] = (
            f"Reconciliation mismatch {m.symbol}: broker holds {m.broker_qty}, lots {m.lot_qty}",
            {"symbol": m.symbol, "broker_qty": m.broker_qty, "lot_qty": m.lot_qty},
        )
    for coid in report.unknown_orders:
        found[f"order:{coid}"] = (
            f"Broker order {coid} is ours (cta-) but unknown to the database; not recreated",
            {"client_order_id": coid},
        )
    if report.error:
        found["error"] = (
            f"Reconciliation could not read the broker: {report.error}",
            {"error": report.error},
        )
    return found


class Reconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        broker: BrokerClient,
        consumer: BrokerEventConsumer,
        flags: FlagService,
        alerts: AlertService,
        config: ConfigService,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._broker = broker
        self._consumer = consumer
        self._flags = flags
        self._alerts = alerts
        self._config = config
        self._clock = clock
        self._lock = asyncio.Lock()
        self._alerted: set[str] = set()  # findings already alerted; a repeat run stays quiet
        self._debounce: asyncio.Task[None] | None = None
        self.last: ReconcileReport | None = None  # F049 health metrics

    async def run(self, settle_s: float = 0, *, release: bool = True) -> ReconcileReport:
        """`release=False` (startup) keeps the block until the sequence reaches step 11."""
        async with self._lock:
            report = replace(await self._reconcile(settle_s), at=self._clock.now())
            await self._settle(report, release)
            self.last = report
        return report

    async def run_periodic(self, interval_s: float = 60.0) -> None:
        """Lifespan task: every minute during regular hours (TRD §9)."""
        while True:
            if is_regular_hours(self._clock.now()):
                try:
                    await self.run(PERIODIC_SETTLE_S)
                except Exception:
                    logger.exception("periodic reconciliation failed")
            await asyncio.sleep(interval_s)

    async def on_fill(self, _applied: Applied) -> None:
        """Consumer hook: one run 2 s after a fill; fills in that window share it."""
        if self._debounce is None:
            self._debounce = asyncio.create_task(self._after_fill(), name="reconcile-after-fill")

    def stop(self) -> None:
        if self._debounce is not None:
            self._debounce.cancel()

    async def _after_fill(self) -> None:
        await asyncio.sleep(AFTER_FILL_DEBOUNCE_S)
        self._debounce = None  # later fills schedule the next run
        try:
            await self.run(PERIODIC_SETTLE_S)
        except Exception:
            logger.exception("post-fill reconciliation failed")

    async def _reconcile(self, settle_s: float) -> ReconcileReport:
        cfg = self._config.get() or RuntimeConfig()
        if cfg.trading_mode == TradingMode.SHADOW:
            # OD-33 (proposal): shadow orders live only in the in-memory SimulatedBroker, lost on
            # restart, so the database is the truth there and there is nothing to compare.
            return ReconcileReport(skipped="shadow")
        try:
            broker_orders = await self._broker.open_orders()
            synced = await self._sync_orders(timedelta(seconds=settle_s))
            positions = await self._broker.positions()
        except Exception as exc:
            logger.exception("reconciliation could not read the broker")
            return ReconcileReport(error=f"{type(exc).__name__}: {exc}")
        ours = [
            o.client_order_id
            for o in broker_orders
            if o.client_order_id.startswith(CLIENT_ID_PREFIX)
        ]
        async with self._sessions() as db:
            lots = await ledger.open_lots(db)
            known = await orders_repo.known_client_ids(db, ours) if ours else set()
        # ponytail: a fill between the broker read and the lot read can show as a one-run
        # mismatch; the next run (post-fill hook) clears it. Add a re-check if it gets noisy.
        report = replace(compare(positions, lots, broker_orders, known), orders_synced=synced)
        if report.foreign:
            async with in_transaction(self._sessions, None) as db:
                for change in report.foreign:
                    await _apply_foreign(db, change)
        return report

    async def _sync_orders(self, settle: timedelta) -> int:
        """Adopt the broker's view of every order we sent (never a resubmit, FR-22)."""
        async with self._sessions() as db:
            pending = await orders_repo.orders_to_sync(db)
        now, synced = self._clock.now(), 0
        for order, applied_qty in pending:
            found = await self._broker.get_by_client_id(order.client_order_id)
            if found is None:
                logger.warning("broker has no such order", extra={"coid": order.client_order_id})
                continue
            events = order_events(order, applied_qty, found, now)
            if events and settle and now - order.updated_at < settle:
                continue
            for event in events:
                await self._consumer.handle(event, OrderEventSource.RECONCILE)
            synced += bool(events)
        return synced

    async def release(self) -> None:
        """Lift `entries_blocked` if startup or a reconciliation set it (never a manual block)."""
        if await self._flags.active_reason(FlagName.ENTRIES_BLOCKED) in SELF_CLEARED_REASONS:
            await self._flags.set(
                FlagName.ENTRIES_BLOCKED, False, reason="reconciliation clean", actor=Actor.SYSTEM
            )

    async def _settle(self, report: ReconcileReport, release: bool) -> None:
        findings = _findings(report)
        for signature in findings.keys() - self._alerted:
            message, details = findings[signature]
            await self._alerts.raise_(
                AlertKind.RECONCILE_MISMATCH, Severity.URGENT, message, details=details
            )
        self._alerted = set(findings)
        if not report.clean:
            await self._flags.set(
                FlagName.ENTRIES_BLOCKED, True, reason="reconciliation", actor=Actor.SYSTEM
            )
        elif release:
            await self.release()


async def _apply_foreign(db: AsyncSession, change: ForeignChange) -> None:
    if change.lot_id is None:
        assert change.instrument is not None  # noqa: S101 — a new lot comes from a position
        await ledger.create_foreign_lot(
            db, change.instrument, change.qty, change.avg, reason="reconciliation"
        )
        return
    lot = await ledger.lock_lot(db, change.lot_id)
    assert lot is not None  # noqa: S101 — read a moment ago; lots are never deleted
    update = LotUpdate(LotChange.RECONCILE_ADJUST, lot.qty, change.qty, lot.avg_price, change.avg)
    await ledger.record_lot(db, update, row=lot, reason="reconciliation")
