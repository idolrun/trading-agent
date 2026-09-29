"""`CardExecutor`: an approved card -> `OrderIntent`s -> `submit_order()` (F036).

Minimal policies; F041 (entries) and F042 (stops / TPs / brackets) replace them:
entry/add = one limit at the approved limit; trim/exit/close = one limit at the mid;
stop / tp cards = the lot's bracket (F042 `BracketService`).
Every order carries the approval (AC-2); ids derive from (card, action_seq) (AC-7).
"""

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cards.models import Card, CardAction
from app.decide.types import ProposedAction
from app.execution.brackets import BracketService
from app.execution.entry import EntryPolicy
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
        entry: EntryPolicy | None = None,
        brackets: BracketService | None = None,
    ) -> None:
        self._sessions = sessions
        self._orders = orders
        self._md = marketdata
        self.entry = entry
        self.brackets = brackets

    async def execute(
        self, card: Card, proposal: ProposedAction, approval_id: uuid.UUID, target: Target
    ) -> list[Order]:
        auth = Approved(approval_id)
        if proposal.action in (CardAction.STOP, CardAction.TP):  # F042: the lot's bracket
            if self.brackets is None or target.lot_id is None:
                raise ExecutionBlocked("stop / take-profit needs an open lot")
            if proposal.action == CardAction.STOP:
                if proposal.stop is None:
                    raise ExecutionBlocked("stop card without a stop")
                return await self.brackets.protect(target.lot_id, card.card_id, stop=proposal.stop)
            levels = [leg.level for leg in proposal.tp_legs]
            return await self.brackets.protect(target.lot_id, card.card_id, tp_levels=levels)
        orders = []
        for intent in await self.intents(card, proposal, target):
            order = await self._orders.submit_order(intent, auth)
            if intent.kind == OrderKind.ENTRY and self.entry is not None:
                await self.entry.watch(order)  # F041: cancel the remainder after M s
            orders.append(order)
        return orders

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
        return []  # review cards place nothing; stop/TP cards go through brackets

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
