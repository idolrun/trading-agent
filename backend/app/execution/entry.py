"""Entry execution (F041, PRD 7.1 steps 6-7, C22, FR-21).

The approved entry is one limit buy at the approved limit (signal price or top of range + X%).
After `entry_cancel_after_s` any unfilled remainder is cancelled; partial fills stay in the lot.
Bracket legs for what actually filled are placed by the fill hook (F042 `protect_lot`).
"""

import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.execution.models import OPEN_ORDER_STATUSES, Order
from app.execution.submit import OrderService
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.timers import repo as timers
from app.timers.models import Timer, TimerKind

logger = logging.getLogger(__name__)


class EntryPolicy:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        orders: OrderService,
        config: ConfigService,
        alerts: AlertService,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._orders = orders
        self._config = config
        self._alerts = alerts
        self._clock = clock

    async def watch(self, order: Order) -> None:
        """C22: the remainder of a resting entry is cancelled after M seconds."""
        seconds = (self._config.get() or RuntimeConfig()).entry_cancel_after_s
        if order.status not in OPEN_ORDER_STATUSES or seconds is None:
            return  # filled / rejected already; no M = config incomplete = no entries anyway
        async with in_transaction(self._sessions, None) as db:
            await timers.schedule(
                db,
                TimerKind.ENTRY_CANCEL,
                self._clock.now() + timedelta(seconds=seconds),
                f"entry_cancel:{order.order_id}",
                order_id=order.order_id,
            )

    async def on_entry_cancel(self, timer: Timer) -> None:
        """`entry_cancel` timer handler."""
        if timer.order_id is None:
            return
        async with self._sessions() as db:
            order = await db.get(Order, timer.order_id)
        if order is None or order.status not in OPEN_ORDER_STATUSES:
            return  # filled or already cancelled
        order = await self._orders.cancel_order(order.order_id, "entry window over (C22)")
        if order.filled_qty:
            await self._alerts.raise_(
                AlertKind.ENTRY_PARTIAL,
                Severity.INFO,
                f"Entry partially filled {order.filled_qty}/{order.qty}; the rest was cancelled",
                details={"filled": order.filled_qty, "qty": order.qty},
                dedupe_key=f"entry_partial:{order.order_id}",
                card_id=order.card_id,
                lot_id=order.lot_id,
                order_id=order.order_id,
            )
