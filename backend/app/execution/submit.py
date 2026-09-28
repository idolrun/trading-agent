"""`submit_order()`: the only way an order reaches a broker (TRD §1.5, FR-20, FR-22, AC-7).

1. Deterministic client order id; an existing order with that id is returned, never resent.
2. Entries refused while the kill switch / entries-blocked flag is on.
3. Fresh broker position read, then (T5a) lock the lot and check the invariants.
4. Commit the `intent` BEFORE calling the broker; the broker call runs outside any transaction.
5. Timeout -> query by client id: adopt what exists, else void the intent. Never a blind retry.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.broker.errors import BrokerRejected, BrokerTimeout, BrokerUnavailable
from app.broker.port import BrokerClient, BrokerOrder, OrderRequest
from app.db import in_transaction
from app.execution import repo
from app.execution.intent import Approved, Auto, OrderIntent, client_order_id
from app.execution.invariants import LotView, WorkingSells, violations
from app.execution.models import Mode, Order, OrderEventSource, OrderKind, OrderStatus, Side
from app.instruments import OptionContract, broker_symbol
from app.ledger import repo as ledger
from app.ledger.models import Instrument, LotStatus
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService

logger = logging.getLogger(__name__)


class EntriesBlocked(Exception):
    pass


class InvariantViolation(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons


class OrderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        broker: BrokerClient,  # paper / live adapter
        simulated: BrokerClient,  # shadow (never a real broker)
        flags: FlagService,
        alerts: AlertService,
        config: ConfigService,
    ) -> None:
        self._sessions = sessions
        self._brokers = {Mode.SHADOW: simulated, Mode.PAPER: broker, Mode.LIVE: broker}
        self._flags = flags
        self._alerts = alerts
        self._config = config

    async def submit_order(self, intent: OrderIntent, auth: Approved | Auto) -> Order:
        coid = client_order_id(intent, auth)
        async with self._sessions() as db:
            if (existing := await repo.by_client_id(db, coid)) is not None:
                return existing  # AC-7: re-executed node, same id, no second order
        mode = Mode((self._config.get() or RuntimeConfig()).trading_mode)
        broker = self._brokers[mode]
        if intent.kind == OrderKind.ENTRY:
            allowed, why = await self._flags.entries_allowed()
            if not allowed:
                raise EntriesBlocked(why or "entries blocked")
        broker_qty = await self._broker_qty(broker, intent) if intent.side == Side.SELL else None

        async with in_transaction(self._sessions, None) as db:  # T5a
            lot, working = await self._lot(db, intent)
            failed = violations(intent, lot, working, broker_qty)
            order = (
                None if failed else await repo.insert_intent(db, _values(intent, auth, coid, mode))
            )
        if failed:
            await self._alert(
                AlertKind.INVARIANT_BLOCK,
                f"Order blocked by safety invariants: {', '.join(failed)}",
                intent,
                coid,
            )
            raise InvariantViolation(failed)
        if order is None:  # a concurrent run inserted it first
            async with self._sessions() as db:
                existing = await repo.by_client_id(db, coid)
            assert existing is not None  # noqa: S101 — the conflict proves it exists
            return existing
        return await self._send(broker, order, intent, coid)

    async def cancel_order(self, order_id: uuid.UUID, reason: str) -> Order:
        async with self._sessions() as db:
            order = await db.get(Order, order_id)
        if order is None:
            raise LookupError(f"order {order_id} not found")
        if order.broker_order_id is not None:
            await self._brokers[Mode(order.mode)].cancel(order.broker_order_id)
        async with in_transaction(self._sessions, None) as db:
            order = await db.merge(order)
            return await repo.set_status(
                db,
                order,
                OrderStatus.CANCELLED,
                OrderEventSource.CANCEL,
                payload={"reason": reason},
            )

    async def _send(
        self, broker: BrokerClient, order: Order, intent: OrderIntent, coid: str
    ) -> Order:
        request = OrderRequest(
            client_order_id=coid,
            instrument=intent.instrument,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            tif=intent.tif,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
        )
        source = OrderEventSource.SUBMIT
        try:
            placed = await broker.submit(request)
        except (BrokerRejected, BrokerUnavailable) as exc:
            await self._alert(AlertKind.ORDER_FAILED, f"Order not placed: {exc}", intent, coid)
            return await self._finish(order, OrderStatus.REJECTED, source, error=str(exc))
        except BrokerTimeout:
            await self._finish(order, OrderStatus.UNKNOWN, source)
            source = OrderEventSource.TIMEOUT_QUERY
            found = await broker.get_by_client_id(coid)  # FR-22: query, never resubmit
            if found is None:
                await self._alert(
                    AlertKind.ORDER_FAILED,
                    "Order submit timed out and the broker has no such order; intent voided",
                    intent,
                    coid,
                )
                return await self._finish(order, OrderStatus.REJECTED, source, error="timeout")
            placed = found
        return await self._finish(order, placed.status, source, placed=placed)

    async def _finish(
        self,
        order: Order,
        status: OrderStatus,
        source: OrderEventSource,
        *,
        placed: BrokerOrder | None = None,
        error: str | None = None,
    ) -> Order:
        async with in_transaction(self._sessions, None) as db:
            order = await db.merge(order)
            return await repo.set_status(
                db,
                order,
                status,
                source,
                broker_order_id=placed.broker_order_id if placed else None,
                filled_qty=placed.filled_qty if placed else None,
                payload={"error": error} if error else None,
            )

    async def _lot(
        self, db: AsyncSession, intent: OrderIntent
    ) -> tuple[LotView | None, WorkingSells]:
        if intent.lot_id is None:
            return None, WorkingSells()
        row = await ledger.lock_lot(db, intent.lot_id)
        if row is None:
            return None, WorkingSells()
        view = LotView(
            lot_id=row.lot_id,
            provider_position_id=row.provider_position_id,
            broker_symbol=row.broker_symbol,
            qty=row.qty,
            is_open=row.status == LotStatus.OPEN,
        )
        return view, await repo.working_sells(db, row.lot_id)

    async def _broker_qty(self, broker: BrokerClient, intent: OrderIntent) -> int | None:
        """Fresh read, outside any transaction. Unreadable -> None -> the sell is refused."""
        try:
            positions = await broker.positions()
        except Exception:
            logger.exception("broker positions unavailable")
            return None
        symbol = broker_symbol(intent.instrument)
        return sum(p.qty for p in positions if broker_symbol(p.instrument) == symbol)

    async def _alert(self, kind: AlertKind, message: str, intent: OrderIntent, coid: str) -> None:
        await self._alerts.raise_(
            kind,
            Severity.URGENT,
            message,
            details={"client_order_id": coid, "kind": intent.kind, "side": intent.side},
            dedupe_key=f"{kind}:{coid}",
            card_id=intent.card_id,
            lot_id=intent.lot_id,
        )


def _values(intent: OrderIntent, auth: Approved | Auto, coid: str, mode: Mode) -> dict[str, object]:
    return {
        "order_id": uuid.uuid4(),
        "client_order_id": coid,
        "card_id": intent.card_id,
        "approval_id": auth.approval_id if isinstance(auth, Approved) else None,
        "auto_reason": auth.reason if isinstance(auth, Auto) else None,
        "parent_order_id": intent.parent_order_id,
        "oco_group_id": intent.oco_group_id,
        "provider_position_id": intent.provider_position_id,
        "lot_id": intent.lot_id,
        "kind": intent.kind,
        "side": intent.side,
        "instrument": Instrument.OPTION
        if isinstance(intent.instrument, OptionContract)
        else Instrument.STOCK,
        "broker_symbol": broker_symbol(intent.instrument),
        "qty": intent.qty,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "stop_price": intent.stop_price,
        "tif": intent.tif,
        "status": OrderStatus.INTENT,
        "mode": mode,
    }
