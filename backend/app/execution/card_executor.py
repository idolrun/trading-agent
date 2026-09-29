"""`CardExecutor`: an approved card -> `OrderIntent`s -> `submit_order()` (F036).

Minimal policies; F041 (entries) and F042 (stops / TPs / brackets) replace them:
entry/add = one limit at the approved limit; trim/exit/close = one limit at the mid;
stop = broker stop (underlying stops are system-held, F044); tp = one limit per leg.
Every order carries the approval (AC-2); ids derive from (card, action_seq) (AC-7).
"""

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cards.models import Card, CardAction
from app.decide.types import ProposedAction
from app.execution import repo
from app.execution.intent import Approved, OrderIntent
from app.execution.models import Order, OrderKind, OrderType, Side, TimeInForce
from app.execution.submit import OrderService
from app.instruments import Instrument
from app.marketdata.port import MarketData
from app.validate.normalize import mid_price

SELLS = frozenset({CardAction.TRIM, CardAction.EXIT, CardAction.CLOSE})


class ExecutionBlocked(Exception):
    """The card cannot be turned into orders (no quote, no tick, no lot, ...)."""


@dataclass(frozen=True)
class Target:
    instrument: Instrument
    lot_id: uuid.UUID | None
    lot_qty: int | None


class CardExecutor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        orders: OrderService,
        marketdata: MarketData,
    ) -> None:
        self._sessions = sessions
        self._orders = orders
        self._md = marketdata

    async def execute(
        self, card: Card, proposal: ProposedAction, approval_id: uuid.UUID, target: Target
    ) -> list[Order]:
        auth = Approved(approval_id)
        if proposal.action == CardAction.STOP and proposal.stop and proposal.stop.cancel:
            return await self._cancel_stops(target)
        intents = await self.intents(card, proposal, target)
        return [await self._orders.submit_order(i, auth) for i in intents]

    async def intents(self, card: Card, p: ProposedAction, t: Target) -> list[OrderIntent]:
        base = {
            "card_id": card.card_id,
            "instrument": t.instrument,
            "provider_position_id": card.provider_position_id,
            "lot_id": t.lot_id,
        }
        a = p.action
        if a in (CardAction.ENTRY, CardAction.ADD):
            if p.qty is None or p.limit_price is None:
                raise ExecutionBlocked("entry needs qty and limit")
            return [
                OrderIntent(
                    **base,
                    kind=OrderKind.ENTRY,
                    side=Side.BUY,
                    qty=p.qty,
                    order_type=OrderType.LIMIT,
                    limit_price=p.limit_price,
                    tif=TimeInForce.DAY,
                )
            ]
        if a in SELLS:
            qty = p.qty if a != CardAction.CLOSE else (p.qty or t.lot_qty)
            if qty is None:
                raise ExecutionBlocked("exit needs qty")
            return [
                OrderIntent(
                    **base,
                    kind=OrderKind.EXIT,
                    side=Side.SELL,
                    qty=qty,
                    order_type=OrderType.LIMIT,
                    limit_price=await self._mid(t.instrument),
                    tif=TimeInForce.DAY,
                )
            ]
        if a == CardAction.STOP:
            stop = p.stop
            if stop is None or stop.level is None or t.lot_qty is None:
                raise ExecutionBlocked("stop needs a level and an open lot")
            if stop.held_by == "system":
                return []  # ponytail: underlying stops are system-held; F044 watches them
            return [
                OrderIntent(
                    **base,
                    kind=OrderKind.STOP,
                    side=Side.SELL,
                    qty=t.lot_qty,
                    order_type=OrderType.STOP,
                    stop_price=stop.level,
                    tif=TimeInForce.GTC,
                )
            ]
        if a == CardAction.TP:
            return [
                OrderIntent(
                    **base,
                    action_seq=n,
                    kind=OrderKind.TP,
                    side=Side.SELL,
                    qty=leg.qty,
                    order_type=OrderType.LIMIT,
                    limit_price=leg.level,
                    tif=TimeInForce.GTC,
                )
                for n, leg in enumerate(p.tp_legs)
            ]
        return []  # review cards place nothing

    async def _mid(self, instrument: Instrument) -> Decimal:
        """C25: exits start at the mid, on the tick grid. No quote / tick -> blocked (OD-12)."""
        try:
            mid = mid_price(await self._md.quote(instrument))
            if mid is None:
                raise ExecutionBlocked("no quote for the exit price")
            tick = await self._md.tick_size(instrument, mid)
        except ExecutionBlocked:
            raise
        except Exception as exc:
            raise ExecutionBlocked(f"market data unavailable: {exc}") from exc
        return (mid / tick).to_integral_value(ROUND_HALF_UP) * tick

    async def _cancel_stops(self, t: Target) -> list[Order]:
        if t.lot_id is None:
            return []
        async with self._sessions() as db:
            working = await repo.open_orders(db, t.lot_id, OrderKind.STOP)
        return [await self._orders.cancel_order(o.order_id, "stop cancelled") for o in working]
