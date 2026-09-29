"""Exit execution (F043, PRD 7.3 steps 5-6, C25, FR-20(3), FR-21, §9, §11, AC-3).

1. Reduce protection first so protective + exit <= lot (F042 `reduce_protection`).
2. A limit sell at the mid (tick grid) for min(qty, lot).
3. Every `exit_reprice_interval_s`: cancel and re-place the unfilled rest one step toward the
   bid (`Auto(REPRICE)` child of the root order, pre-approved within the intent).
4. At the floor and still unfilled: stop, alert `exit_floor`, restore protection.
5. Filled (fully or partly) and done: restore protection for whatever the lot still holds.

The floor and start mid are written to the root order's history, so after a restart F046 can
`adopt()` a working exit and resume its loop from the DB.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.decide.risk_snapshot import lot_contract
from app.execution.brackets import BracketService
from app.execution.intent import Approved, Auto, OrderIntent
from app.execution.models import (
    OPEN_ORDER_STATUSES,
    AutoReason,
    Fill,
    Order,
    OrderEvent,
    OrderEventSource,
    OrderKind,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from app.execution.pricing import floor_price, next_price, round_to_tick
from app.execution.submit import EntriesBlocked, InvariantViolation, OrderService
from app.instruments import Instrument
from app.ledger.models import Lot, LotStatus
from app.marketdata.port import MarketData
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.validate.normalize import mid_price

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class ExitBlocked(Exception):
    """No quote / tick to price the exit: nothing was sent (OD-12: never guess)."""


class ExitPolicy:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        orders: OrderService,
        brackets: BracketService,
        marketdata: MarketData,
        config: ConfigService,
        alerts: AlertService,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._sessions = sessions
        self._orders = orders
        self._brackets = brackets
        self._md = marketdata
        self._config = config
        self._alerts = alerts
        self.sleep = sleep
        self.loops: dict[uuid.UUID, asyncio.Task[None]] = {}  # root order id -> re-price loop

    async def execute(
        self,
        lot_id: uuid.UUID,
        qty: int,
        auth: Approved | Auto,
        *,
        card_id: uuid.UUID | None = None,
    ) -> Order | None:
        """Start one exit intent; returns its root order (None: nothing left to sell)."""
        await self._brackets.reduce_protection(lot_id, qty)
        async with self._sessions() as db:
            lot = await db.get(Lot, lot_id)
        if lot is None or lot.status != LotStatus.OPEN or lot.qty == 0:
            await self._brackets.restore_protection(lot_id)
            return None  # a stop fired meanwhile: nothing left (no oversell)
        qty = min(qty, lot.qty)
        instrument = lot_contract(lot)
        try:
            mid, _bid, tick = await self._prices(instrument)
        except ExitBlocked:
            await self._brackets.restore_protection(lot_id)
            raise
        cfg = self._config.get() or RuntimeConfig()
        floor = floor_price(mid, cfg.exit_floor_pct or Decimal(0), tick)
        price = max(floor, round_to_tick(mid, tick))
        intent = OrderIntent(
            card_id=card_id,
            kind=OrderKind.EXIT,
            side=Side.SELL,
            instrument=instrument,
            qty=qty,
            order_type=OrderType.LIMIT,
            limit_price=price,
            tif=TimeInForce.DAY,
            provider_position_id=lot.provider_position_id,
            lot_id=lot.lot_id,
        )
        try:
            root = await self._orders.submit_order(intent, auth)
        except (InvariantViolation, EntriesBlocked):
            await self._brackets.restore_protection(lot_id)
            raise
        await self._note(root, {"exit_floor": str(floor), "start_mid": str(mid), "tick": str(tick)})
        self._start(root, floor, tick)
        return root

    async def adopt(self, root_id: uuid.UUID) -> None:
        """F046 restart hook: resume the re-price loop of a working exit from the DB."""
        async with self._sessions() as db:
            root = await db.get(Order, root_id)
            payload = await db.scalar(
                select(col(OrderEvent.broker_payload))
                .where(
                    col(OrderEvent.order_id) == root_id,
                    col(OrderEvent.broker_payload)["exit_floor"].is_not(None),
                )
                .limit(1)
            )
        if root is None or not payload or root_id in self.loops:
            return
        self._start(root, Decimal(payload["exit_floor"]), Decimal(payload["tick"]))

    def _start(self, root: Order, floor: Decimal, tick: Decimal) -> None:
        task = asyncio.create_task(self._loop(root, floor, tick), name=f"exit:{root.order_id}")
        self.loops[root.order_id] = task
        task.add_done_callback(lambda _t: self.loops.pop(root.order_id, None))

    async def _loop(self, root: Order, floor: Decimal, tick: Decimal) -> None:
        try:
            await self._reprice(root, floor, tick)
        except Exception:
            logger.exception("exit re-price loop failed", extra={"order_id": str(root.order_id)})
            await self._alerts.raise_(
                AlertKind.EXIT_FLOOR,
                Severity.URGENT,
                "Exit re-pricing stopped on an error; check the order",
                dedupe_key=f"exit_error:{root.order_id}",
                order_id=root.order_id,
                lot_id=root.lot_id,
            )
            if root.lot_id is not None:
                await self._brackets.restore_protection(root.lot_id)

    async def _reprice(self, root: Order, floor: Decimal, tick: Decimal) -> None:
        assert root.lot_id is not None and root.limit_price is not None  # noqa: S101
        cfg = self._config.get() or RuntimeConfig()
        interval, step_pct = cfg.exit_reprice_interval_s, cfg.exit_reprice_step_pct
        if interval is None or step_pct is None:
            # ponytail: config incomplete -> the exit rests at the mid; entries are disabled then
            return
        current, price, seq = await self._current(root), root.limit_price, 0
        instrument = lot_contract(await self._lot(root.lot_id))
        while True:
            await self.sleep(interval)
            current = await self._refresh(current)
            remaining = await self._remaining(root)
            if remaining <= 0 or current.status not in OPEN_ORDER_STATUSES:
                break  # filled (or ended at the broker): protect what is left
            if price <= floor:
                await self._orders.cancel_order(current.order_id, "exit reached its floor")
                await self._alerts.raise_(
                    AlertKind.EXIT_FLOOR,
                    Severity.URGENT,
                    f"Exit reached its floor {floor} unfilled; protection restored",
                    details={"floor": str(floor), "unfilled": remaining},
                    dedupe_key=f"exit_floor:{root.order_id}",
                    order_id=root.order_id,
                    lot_id=root.lot_id,
                )
                break
            mid, bid, _ = await self._prices(instrument)
            price = next_price(price, mid, bid, step_pct, floor, tick)
            await self._orders.cancel_order(current.order_id, "exit re-price")
            lot = await self._lot(root.lot_id)
            remaining = min(
                await self._remaining(root), lot.qty if lot.status == LotStatus.OPEN else 0
            )
            if remaining <= 0:
                break
            seq += 1
            current = await self._orders.submit_order(
                OrderIntent(
                    card_id=root.card_id,
                    parent_order_id=root.order_id,
                    kind=OrderKind.EXIT,
                    side=Side.SELL,
                    instrument=instrument,
                    qty=remaining,
                    order_type=OrderType.LIMIT,
                    limit_price=price,
                    tif=TimeInForce.DAY,
                    provider_position_id=root.provider_position_id,
                    lot_id=root.lot_id,
                ),
                Auto(AutoReason.REPRICE, seq),
            )
        await self._settled(root)
        await self._brackets.restore_protection(root.lot_id)

    async def _settled(self, root: Order, tries: int = 50) -> None:
        """Wait (bounded) until every fill of the intent reached the lot, so restoring never
        protects contracts already sold (fills arrive through the broker event stream)."""
        chain = (col(Order.order_id) == root.order_id) | (
            col(Order.parent_order_id) == root.order_id
        )
        for _ in range(tries):
            async with self._sessions() as db:
                reported = await db.scalar(
                    select(func.coalesce(func.sum(col(Order.filled_qty)), 0)).where(chain)
                )
                applied = await db.scalar(
                    select(func.coalesce(func.sum(col(Fill.qty)), 0))
                    .join(Order, col(Order.order_id) == col(Fill.order_id))
                    .where(chain)
                )
            if int(applied or 0) >= int(reported or 0):
                return
            await self.sleep(0.1)
        logger.warning(
            "exit fills not applied yet; restoring anyway", extra={"id": str(root.order_id)}
        )

    async def _prices(self, instrument: Instrument) -> tuple[Decimal, Decimal, Decimal]:
        try:
            quote = await self._md.quote(instrument)
            mid = mid_price(quote)
            if mid is None or quote.bid is None:
                raise ExitBlocked("no bid/mid to price the exit")
            tick = await self._md.tick_size(instrument, mid)
        except ExitBlocked:
            raise
        except Exception as exc:
            raise ExitBlocked(f"market data unavailable: {exc}") from exc
        return mid, quote.bid, tick

    async def _remaining(self, root: Order) -> int:
        """Unfilled qty of the whole intent: root qty minus fills of the root and children."""
        async with self._sessions() as db:
            filled = await db.scalar(
                select(func.coalesce(func.sum(col(Order.filled_qty)), 0)).where(
                    (col(Order.order_id) == root.order_id)
                    | (col(Order.parent_order_id) == root.order_id)
                )
            )
        return root.qty - int(filled or 0)

    async def _current(self, root: Order) -> Order:
        """The intent's live order: the latest child, else the root."""
        async with self._sessions() as db:
            child = await db.scalar(
                select(Order)
                .where(col(Order.parent_order_id) == root.order_id)
                .order_by(col(Order.created_at).desc())
                .limit(1)
            )
        return child or root

    async def _refresh(self, order: Order) -> Order:
        async with self._sessions() as db:
            fresh = await db.get(Order, order.order_id)
        assert fresh is not None  # noqa: S101
        return fresh

    async def _lot(self, lot_id: uuid.UUID) -> Lot:
        async with self._sessions() as db:
            lot = await db.get(Lot, lot_id)
        assert lot is not None  # noqa: S101 — orders reference their lot
        return lot

    async def _note(self, order: Order, payload: dict[str, str]) -> None:
        async with in_transaction(self._sessions, None) as db:
            db.add(
                OrderEvent(
                    order_id=order.order_id,
                    status=OrderStatus(order.status),
                    source=OrderEventSource.SUBMIT,
                    broker_payload=payload,
                )
            )
