"""Manual operations (F047, PRD 7.9, C40, C42, C77).

Every manual order is a `source='manual'` card approved by the user's own action, then run
through the normal approval graph: the same revalidation, FR-20 invariants and audit as a
signal order (C42, AC-2). Nothing here calls a broker, and no model is involved. A repeated
`idempotencyKey` finds its card again (`card_id = uuid5(dedupe_key)`) and orders nothing new.
"""

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.cards.approval_service import ApprovalService, DecisionConflict, DecisionInvalid
from app.cards.edits import Edits
from app.cards.models import (
    Card,
    CardAction,
    CardEvent,
    CardReason,
    CardSource,
    CardState,
    CardType,
)
from app.cards.repo import card_id_for
from app.db import in_transaction
from app.decide.types import ProposedAction, StopProposal
from app.execution.brackets import BracketService
from app.execution.models import Order
from app.execution.stops_repo import active as active_stops
from app.instruments import Instrument, OptionContract
from app.ledger import repo as ledger
from app.ledger.lots import LotUpdate
from app.ledger.models import Instrument as Kind
from app.ledger.models import Lot, LotChange, LotStatus
from app.marketdata.port import MarketData
from app.markettime import Clock
from app.pipeline.apply import insert_card
from app.reconcile.service import Reconciler
from app.runtime_config.models import Actor
from app.system.flags import FlagService
from app.system.models import FlagName
from app.validate.identity import find_in_chain

logger = logging.getLogger(__name__)
Scope = Literal["options", "stocks", "all"]
StopOp = Literal["add", "move", "cancel"]
SCOPES = {
    "options": {Kind.OPTION},
    "stocks": {Kind.STOCK},
    "all": {Kind.OPTION, Kind.STOCK},
}


class ManualNotFound(LookupError):
    pass


class ManualInvalid(ValueError):
    """The request or one of the shared checks refuses it -> 422."""


@dataclass(frozen=True)
class ManualResult:
    card_id: uuid.UUID
    state: CardState
    order_ids: list[uuid.UUID]
    lot_id: uuid.UUID | None = None
    blocked: str | None = None  # why revalidation refused it (the card is then invalidated)


async def _consumed() -> bool:
    return True  # the route's `StepUpDep` already used the password check


class ManualService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        approvals: ApprovalService,
        brackets: BracketService,
        reconciler: Reconciler,
        flags: FlagService,
        marketdata: MarketData,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._approvals = approvals
        self._brackets = brackets
        self._reconciler = reconciler
        self._flags = flags
        self._md = marketdata
        self._clock = clock

    # --- orders (all through a manual card) ----------------------------------------------

    async def exit(
        self, lot_id: uuid.UUID, qty: int, key: str, session_id: uuid.UUID
    ) -> ManualResult:
        """Any open lot, foreign ones too (C47: the one place they can be sold)."""
        if (found := await self._existing(key, session_id)) is not None:
            return _strict(found)
        lot = await self._open_lot(lot_id)
        if qty > lot.qty:
            raise ManualInvalid(f"qty {qty} exceeds the lot ({lot.qty})")
        return _strict(await self._exit(lot, qty, key, session_id))

    async def stop(
        self,
        lot_id: uuid.UUID,
        op: StopOp,
        level: Decimal | None,
        basis: Literal["premium", "underlying"] | None,
        key: str,
        session_id: uuid.UUID,
    ) -> ManualResult:
        if (found := await self._existing(key, session_id)) is not None:
            return _strict(found)
        lot = await self._open_lot(lot_id)
        if lot.is_foreign:
            raise ManualInvalid("foreign lots are read-only (C47): exit them instead")
        if op != "cancel" and (level is None or level <= 0):
            raise ManualInvalid(f"{op} needs a stop level")
        async with self._sessions() as db:
            has_stop = any(s.kind == "stop" for s in await active_stops(db, lot_id))
        if op == "add" and has_stop:
            raise ManualInvalid("the lot already has a stop: use move")
        if op != "add" and not has_stop:
            raise ManualInvalid(f"no stop to {op}")
        underlying = basis == "underlying" and lot.instrument == Kind.OPTION  # as decide (C49)
        stop = (
            StopProposal(cancel=True)
            if op == "cancel"
            else StopProposal(
                level=level,
                basis="underlying" if underlying else "premium",
                held_by="system" if underlying else "broker",
            )
        )
        proposal = ProposedAction(action=CardAction.STOP, card_type=CardType.NORMAL, stop=stop)
        return _strict(await self._act(key, proposal, lot, session_id))

    async def entry(
        self,
        contract: Instrument,
        qty: int,
        limit_price: Decimal,
        key: str,
        session_id: uuid.UUID,
    ) -> ManualResult:
        """F019 identity + tick here; F024 gates, quote and hours at approval (C42): the
        card is revalidated exactly like a signal entry, and a refusal is a 422."""
        if (found := await self._existing(key, session_id)) is not None:
            return _strict(found)
        await self._check_contract(contract, limit_price)
        proposal = ProposedAction(
            action=CardAction.ENTRY,
            card_type=CardType.NORMAL,
            qty=qty,
            limit_price=limit_price,
            contract=contract,
        )
        return _strict(await self._act(key, proposal, None, session_id, stepped_up=True))

    # --- kill switch, flatten ------------------------------------------------------------

    async def kill_switch(self, active: bool, reason: str, session_id: uuid.UUID) -> bool:
        """OD-27: disables entry/add cards and entries; exits and stops stay possible."""
        await self._flags.set(
            FlagName.KILL_SWITCH, active, reason, Actor.USER, session_id=session_id
        )
        return await self._flags.is_active(FlagName.KILL_SWITCH)

    async def flatten(self, scope: Scope, key: str, session_id: uuid.UUID) -> list[ManualResult]:
        """Kill switch first, then one manual exit per open TRACKED lot in scope (OD-31)."""
        await self.kill_switch(True, f"flatten {scope}", session_id)
        async with self._sessions() as db:
            lots = [
                lot
                for lot in await ledger.open_lots(db)
                if not lot.is_foreign and lot.instrument in SCOPES[scope]
            ]
        results = []
        for lot in lots:
            lot_key = f"{key}:{lot.lot_id}"
            found = await self._existing(lot_key, session_id, stepped_up=True)
            results.append(
                found or await self._exit(lot, lot.qty, lot_key, session_id, stepped_up=True)
            )
        return results

    # --- lot adjust ----------------------------------------------------------------------

    async def adjust_lot(self, lot_id: uuid.UUID, qty: int, reason: str) -> None:
        """F046 resolution: set the lot to the broker's quantity, protect what is left, then
        reconcile (a clean run lifts `entries_blocked`)."""
        async with in_transaction(self._sessions, None) as db:
            lot = await ledger.lock_lot(db, lot_id)
            if lot is None:
                raise ManualNotFound("lot not found")
            if lot.status != LotStatus.OPEN:
                raise ManualInvalid("lot is closed")
            update = LotUpdate(
                LotChange.RECONCILE_ADJUST, lot.qty, qty, lot.avg_price, lot.avg_price
            )
            await ledger.record_lot(db, update, row=lot, reason=f"manual adjust: {reason}")
        await self._brackets.rebalance(lot_id)  # stops/TPs never exceed the lot (FR-20)
        await self._reconciler.run()

    # --- internals -----------------------------------------------------------------------

    async def _exit(
        self, lot: Lot, qty: int, key: str, session_id: uuid.UUID, *, stepped_up: bool = False
    ) -> ManualResult:
        proposal = ProposedAction(action=CardAction.EXIT, card_type=CardType.NORMAL, qty=qty)
        return await self._act(key, proposal, lot, session_id, stepped_up=stepped_up)

    async def _act(
        self,
        key: str,
        proposal: ProposedAction,
        lot: Lot | None,
        session_id: uuid.UUID,
        *,
        stepped_up: bool = False,
    ) -> ManualResult:
        async with in_transaction(self._sessions, None) as db:
            await insert_card(
                db,
                proposal,
                key=f"manual:{key}",
                source=CardSource.MANUAL,
                signal_id=None,
                provider_id=lot.provider_id if lot else None,
                position_id=lot.provider_position_id if lot else None,
                lot_id=lot.lot_id if lot else None,
                posted_at=self._clock.now(),
            )
        card_id = card_id_for(f"manual:{key}")
        await self._decide(card_id, session_id, stepped_up)
        return await self._result(card_id)

    async def _existing(
        self, key: str, session_id: uuid.UUID, *, stepped_up: bool = False
    ) -> ManualResult | None:
        """Idempotency: the card of an earlier request with this key (finished if that one
        stopped before the approval)."""
        card_id = card_id_for(f"manual:{key}")
        async with self._sessions() as db:
            card = await db.get(Card, card_id)
        if card is None:
            return None
        if card.state == CardState.PENDING:
            await self._decide(card_id, session_id, stepped_up)
        return await self._result(card_id)

    async def _decide(self, card_id: uuid.UUID, session_id: uuid.UUID, stepped_up: bool) -> None:
        """The user's explicit action is the approval; the graph revalidates and executes."""
        try:
            await self._approvals.decide(
                card_id,
                "approve",
                1,
                Edits(),
                session_id=session_id,
                consume_step_up=_consumed,
                stepped_up=stepped_up,
            )
        except DecisionConflict:
            pass  # a concurrent request with the same key decided it first
        except DecisionInvalid as exc:
            raise ManualInvalid(str(exc)) from None

    async def _result(self, card_id: uuid.UUID) -> ManualResult:
        async with self._sessions() as db:
            card = await db.get(Card, card_id)
            assert card is not None  # noqa: S101 — inserted by this request or an earlier one
            orders = await db.scalars(
                select(col(Order.order_id))
                .where(col(Order.card_id) == card_id)
                .order_by(col(Order.created_at))
            )
            blocked = None
            if card.state == CardState.INVALIDATED:
                blocked = await _invalidation_reason(db, card_id)
            return ManualResult(card_id, card.state, list(orders.all()), card.lot_id, blocked)

    async def _open_lot(self, lot_id: uuid.UUID) -> Lot:
        async with self._sessions() as db:
            lot = await db.get(Lot, lot_id)
        if lot is None:
            raise ManualNotFound("lot not found")
        if lot.status != LotStatus.OPEN:
            raise ManualInvalid("lot is closed")
        return lot

    async def _check_contract(self, contract: Instrument, limit_price: Decimal) -> None:
        try:
            if isinstance(contract, OptionContract):
                chain = await self._md.chain(contract.underlying, contract.expiry)
                if find_in_chain(chain, contract.strike, contract.right) is None:
                    raise ManualInvalid("contract_not_in_chain")
            else:
                await self._md.quote(contract)
            tick = await self._md.tick_size(contract, limit_price)
        except ManualInvalid:
            raise
        except Exception:
            logger.exception("manual entry: market data unavailable")
            raise ManualInvalid("market_data_unavailable") from None  # OD-12: never guess
        if limit_price % tick != 0:
            raise ManualInvalid(f"price_not_on_tick: {tick}")


def _strict(result: ManualResult) -> ManualResult:
    if result.blocked is not None:
        raise ManualInvalid(result.blocked)
    return result


async def _invalidation_reason(db: AsyncSession, card_id: uuid.UUID) -> str:
    detail = await db.scalar(
        select(col(CardEvent.detail))
        .where(
            col(CardEvent.card_id) == card_id,
            col(CardEvent.reason_code) == CardReason.REVALIDATE_FAILED,
        )
        .order_by(col(CardEvent.id).desc())
        .limit(1)
    )
    return str((detail or {}).get("reason", "revalidation failed"))
