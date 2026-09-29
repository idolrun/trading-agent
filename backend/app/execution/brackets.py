"""Stops, take-profits and brackets (F042, PRD 7.4, FR-23, C28, C29, C49, C50, C61, C83, AC-16).

A lot's protection is a set of legs: at most one stop (premium -> broker `stop`; stock -> broker
`stop_market` GTC; underlying -> a `stops` row held by the system, F044) and TP limit legs split
over the lot per C83. All broker legs of a lot share one `oco_group_id` (OD-30, confirmed
2026-09-29): the stop and the TPs are alternatives, so the invariant counts max(stop, sum TP).
The linkage is system-managed: every fill re-converges the legs to the lot (a TP fill shrinks
the stop, a stop fill cancels the TPs). A real adapter should use native OCO (F051) to close
the race of two legs filling at once.

`converge` is the one path: read legs -> desired legs -> cancel what differs -> place what is
missing. Replacements carry `parent_order_id`; resizes run under the bracket's approval, a
restore after an unfilled exit is the pre-approved `stop_restore` (PRD §9).
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.cards.models import Approval, Decision
from app.db import in_transaction
from app.decide.risk_snapshot import lot_contract
from app.decide.rules import tp_legs
from app.decide.types import ProposedAction, StopProposal
from app.execution import stops_repo
from app.execution.fills import Applied
from app.execution.intent import Approved, Auto, OrderIntent
from app.execution.models import (
    OPEN_ORDER_STATUSES,
    AutoReason,
    HeldBy,
    Order,
    OrderKind,
    OrderStatus,
    OrderType,
    Side,
    Stop,
    StopChange,
    StopKind,
    StopLevelBasis,
    StopStatus,
    TimeInForce,
)
from app.execution.submit import OrderService
from app.ledger.models import Instrument, Lot, LotStatus

logger = logging.getLogger(__name__)
NAMESPACE = uuid.UUID("5a0f6e2d-9b3c-4d71-8e25-6c1f0b7a3d94")


@dataclass(frozen=True)
class StopSpec:
    level: Decimal
    basis: StopLevelBasis
    held_by: HeldBy
    candle_close: bool = False  # information only (C49)


@dataclass(frozen=True)
class Leg:
    kind: StopKind
    tp_index: int | None
    level: Decimal
    qty: int
    card_id: uuid.UUID
    basis: StopLevelBasis
    held_by: HeldBy
    candle_close: bool = False

    @property
    def key(self) -> tuple[StopKind, int | None]:
        return self.kind, self.tp_index


class _Keep:
    pass


KEEP = _Keep()  # leave that part of the bracket as it is


def size_legs(
    stop: tuple[uuid.UUID, StopSpec] | None,
    tps: list[tuple[int, Decimal, uuid.UUID]],
    lot_qty: int,
    cap: int | None = None,
) -> list[Leg]:
    """Pure. The stop covers the (capped) lot; TPs split it per C83 in index order. So in the
    one OCO group max(stop, sum TP) <= min(lot, cap). Legs sized to 0 are left out."""
    limit = max(0, min(lot_qty, lot_qty if cap is None else cap))
    legs = []
    if stop is not None and limit > 0:
        card_id, spec = stop
        legs.append(
            Leg(
                StopKind.STOP,
                None,
                spec.level,
                limit,
                card_id,
                spec.basis,
                spec.held_by,
                spec.candle_close,
            )
        )
    split = tp_legs([level for _, level, _ in tps], limit)
    for (index, level, card_id), leg in zip(tps, split, strict=False):
        legs.append(
            Leg(StopKind.TP, index, level, leg.qty, card_id, StopLevelBasis.PREMIUM, HeldBy.BROKER)
        )
    return legs


def stop_spec(proposal: StopProposal, lot: Lot) -> StopSpec | None:
    """The card's stop for this lot; None = cancel ("pulled stop")."""
    if proposal.cancel or proposal.level is None:
        return None
    if proposal.held_by == "system":
        basis, held_by = StopLevelBasis.UNDERLYING, HeldBy.SYSTEM
    elif lot.instrument == Instrument.STOCK:
        basis, held_by = StopLevelBasis.STOCK, HeldBy.BROKER  # stop-market GTC (C61)
    else:
        basis, held_by = StopLevelBasis.PREMIUM, HeldBy.BROKER
    return StopSpec(proposal.level, basis, held_by, proposal.candle_close)


class BracketService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, orders: OrderService) -> None:
        self._sessions = sessions
        self._orders = orders
        # ponytail: in-process per-lot lock (one app process); a DB advisory lock if we scale out
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def protect(
        self,
        lot_id: uuid.UUID,
        card_id: uuid.UUID,
        *,
        stop: StopProposal | _Keep | None = KEEP,
        tp_levels: list[Decimal] | _Keep = KEEP,
    ) -> list[Order]:
        """Set the approved card's stop and/or TP levels on the lot (entry bracket, stop and
        TP cards). `stop=None` cancels the stop."""
        return await self.converge(lot_id, card_id=card_id, stop=stop, tp_levels=tp_levels)

    async def rebalance(self, lot_id: uuid.UUID) -> list[Order]:
        return await self.converge(lot_id)

    async def reduce_protection(self, lot_id: uuid.UUID, exit_qty: int) -> None:
        """FR-20(3): before an exit, protective + exit <= lot; shrunk-away legs are suspended."""
        async with self._sessions() as db:
            lot = await db.get(Lot, lot_id)
        qty = lot.qty if lot is not None else 0
        await self.converge(lot_id, cap=max(0, qty - exit_qty))

    async def restore_protection(self, lot_id: uuid.UUID) -> list[Order]:
        """After an exit ended (filled, partial or at the floor): protect what is left."""
        return await self.converge(lot_id, restore=True)

    async def on_fill(self, applied: Applied) -> None:
        """`BrokerEventConsumer` hook: entry fills get the approved bracket for the filled
        qty (F041, C83 on filled qty); protective fills re-converge the other legs."""
        order, lot = applied.order, applied.lot
        if lot is None:
            return
        if order.kind == OrderKind.ENTRY and order.card_id is not None:
            proposal = await self._approved(order.card_id)
            if proposal is None:
                return
            await self.protect(
                lot.lot_id,
                order.card_id,
                stop=proposal.stop if proposal.stop is not None else KEEP,
                tp_levels=[leg.level for leg in proposal.tp_legs] if proposal.tp_legs else KEEP,
            )
        elif order.kind in (OrderKind.STOP, OrderKind.TP):
            if order.filled_qty >= order.qty:
                async with in_transaction(self._sessions, None) as db:
                    row = await stops_repo.by_order(db, order.order_id)
                    if row is not None and row.status == StopStatus.WORKING:
                        await stops_repo.set_status(db, row, StopStatus.FIRED, StopChange.FIRE)
            await self.rebalance(lot.lot_id)

    async def converge(
        self,
        lot_id: uuid.UUID,
        *,
        card_id: uuid.UUID | None = None,
        stop: StopProposal | _Keep | None = KEEP,
        tp_levels: list[Decimal] | _Keep = KEEP,
        cap: int | None = None,
        restore: bool = False,
    ) -> list[Order]:
        async with self._locks[lot_id]:
            return await self._converge(lot_id, card_id, stop, tp_levels, cap, restore)

    async def _converge(
        self,
        lot_id: uuid.UUID,
        card_id: uuid.UUID | None,
        stop: StopProposal | _Keep | None,
        tp_levels: list[Decimal] | _Keep,
        cap: int | None,
        restore: bool,
    ) -> list[Order]:
        async with self._sessions() as db:
            lot = await db.get(Lot, lot_id)
            rows = await stops_repo.active(db, lot_id)
            open_orders = {
                o.order_id: o
                for o in (
                    await db.scalars(
                        select(Order).where(
                            col(Order.order_id).in_([r.order_id for r in rows if r.order_id])
                        )
                    )
                ).all()
            }
        if lot is None:
            return []
        stop_now = next((r for r in rows if r.kind == StopKind.STOP), None)
        stop_leg = (
            (
                stop_now.card_id,
                StopSpec(
                    stop_now.level,
                    StopLevelBasis(stop_now.basis),
                    HeldBy(stop_now.held_by),
                    stop_now.candle_close,
                ),
            )
            if stop_now is not None
            else None
        )
        tps = [(r.tp_index or 0, r.level, r.card_id) for r in rows if r.kind == StopKind.TP]
        if not isinstance(stop, _Keep):
            assert card_id is not None  # noqa: S101 — protect() passes it
            spec = stop_spec(stop, lot) if stop is not None else None
            stop_leg = (card_id, spec) if spec is not None else None
        if not isinstance(tp_levels, _Keep):
            assert card_id is not None  # noqa: S101
            tps = [(i + 1, level, card_id) for i, level in enumerate(tp_levels)]
        lot_qty = lot.qty if lot.status == LotStatus.OPEN else 0
        wanted = {leg.key: leg for leg in size_legs(stop_leg, tps, lot_qty, cap)}
        specified: set[tuple[StopKind, int | None]] = {(StopKind.STOP, None)} if stop_leg else set()
        specified |= {(StopKind.TP, index) for index, _, _ in tps}

        parents: dict[tuple[StopKind, int | None], uuid.UUID | None] = {}
        for row in rows:
            key = (StopKind(row.kind), row.tp_index)
            leg = wanted.get(key)
            if leg is not None and _satisfies(
                row, leg, open_orders.get(row.order_id) if row.order_id else None
            ):
                del wanted[key]  # already in place
                continue
            if row.status == StopStatus.SUSPENDED and leg is None and key in specified and lot_qty:
                continue  # stays suspended
            await self._cancel_order(row, open_orders)
            suspend = leg is None and key in specified and lot_qty > 0  # closed: cancel all
            async with in_transaction(self._sessions, None) as db:
                row = await db.merge(row)
                await stops_repo.set_status(
                    db,
                    row,
                    StopStatus.SUSPENDED if suspend else StopStatus.CANCELLED,
                    StopChange.SUSPEND if suspend else StopChange.CANCEL,
                )
            parents[key] = row.order_id

        placed = []
        for leg in wanted.values():  # dict order: the stop first, then TPs by index
            order = await self._place(lot, leg, parents.get(leg.key), restore)
            if order is not None:
                placed.append(order)
        return placed

    async def _place(
        self, lot: Lot, leg: Leg, parent: uuid.UUID | None, restore: bool
    ) -> Order | None:
        order = None
        if leg.held_by == HeldBy.BROKER:
            intent = OrderIntent(
                card_id=leg.card_id,
                action_seq=1 if leg.kind == StopKind.STOP else 1 + (leg.tp_index or 0),
                parent_order_id=parent,
                kind=OrderKind.STOP if leg.kind == StopKind.STOP else OrderKind.TP,
                side=Side.SELL,
                instrument=lot_contract(lot),
                qty=leg.qty,
                order_type=_order_type(leg),
                limit_price=leg.level if leg.kind == StopKind.TP else None,
                stop_price=leg.level if leg.kind == StopKind.STOP else None,
                tif=TimeInForce.GTC,
                provider_position_id=lot.provider_position_id,
                lot_id=lot.lot_id,
                oco_group_id=uuid.uuid5(NAMESPACE, f"bracket:{lot.lot_id}"),
            )
            if restore and parent is not None:
                auth: Approved | Auto = Auto(AutoReason.STOP_RESTORE)
            else:
                approval = await self._approval_id(leg.card_id)
                if approval is None:
                    logger.error(
                        "protective leg without an approval", extra={"card": str(leg.card_id)}
                    )
                    return None
                auth = Approved(approval)
            order = await self._orders.submit_order(intent, auth)
            if order.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                return order  # alerted by submit_order; the leg stays unplaced
        async with in_transaction(self._sessions, None) as db:
            await stops_repo.add(
                db,
                Stop(
                    lot_id=lot.lot_id,
                    kind=leg.kind,
                    basis=leg.basis,
                    held_by=leg.held_by,
                    level=leg.level,
                    qty=leg.qty,
                    tp_index=leg.tp_index,
                    order_id=order.order_id if order else None,
                    card_id=leg.card_id,
                    candle_close=leg.candle_close,
                    status=StopStatus.WORKING,
                ),
                StopChange.RESTORE if restore else StopChange.CREATE,
            )
        return order

    async def _cancel_order(self, row: Stop, orders: dict[uuid.UUID, Order]) -> None:
        order = orders.get(row.order_id) if row.order_id else None
        if order is not None and order.status in OPEN_ORDER_STATUSES:
            await self._orders.cancel_order(order.order_id, "bracket resized / replaced")

    async def _approval_id(self, card_id: uuid.UUID) -> uuid.UUID | None:
        async with self._sessions() as db:
            found: uuid.UUID | None = await db.scalar(
                select(col(Approval.approval_id)).where(
                    col(Approval.card_id) == card_id, col(Approval.decision) == Decision.APPROVE
                )
            )
        return found

    async def _approved(self, card_id: uuid.UUID) -> ProposedAction | None:
        async with self._sessions() as db:
            approval = await db.scalar(select(Approval).where(col(Approval.card_id) == card_id))
        if approval is None or approval.approved_proposal is None:
            return None
        return ProposedAction.model_validate(approval.approved_proposal)


def _satisfies(row: Stop, leg: Leg, order: Order | None) -> bool:
    if row.status != StopStatus.WORKING or row.level != leg.level:
        return False
    if row.held_by == HeldBy.SYSTEM:
        return leg.held_by == HeldBy.SYSTEM and row.qty == leg.qty
    return (
        order is not None
        and order.status in OPEN_ORDER_STATUSES
        and order.qty - order.filled_qty == leg.qty
    )


def _order_type(leg: Leg) -> OrderType:
    if leg.kind == StopKind.TP:
        return OrderType.LIMIT
    return OrderType.STOP_MARKET if leg.basis == StopLevelBasis.STOCK else OrderType.STOP
