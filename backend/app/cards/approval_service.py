"""Human in the loop for every card (F036, C14, PRD G3 / §9 / 7.1 step 5 / 7.2).

`decide` is transaction T4: lock the card, check `pending` + `version`, write the `approvals`
row and the transition, commit, THEN resume the card's approval graph. Timer expiry,
supersession and restart resume the same graph. Approved cards are revalidated (C22, entry
gates) before `CardExecutor` submits through `submit_order()`.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.cards import repo as cards
from app.cards.edits import APPROVE_EDITS, EditInvalid, Edits, apply_edits
from app.cards.models import (
    Approval,
    Card,
    CardAction,
    CardActor,
    CardReason,
    CardSource,
    CardState,
    Decision,
)
from app.cards.state import IllegalTransition
from app.cards.validity import ENTRY_ACTIONS, approval_blockers, check_entry, check_exit
from app.db import in_transaction, notify
from app.decide.risk import entry_block_reason
from app.decide.risk_snapshot import build_risk_snapshot, lot_contract
from app.decide.types import FlagsView, ProposedAction
from app.execution.card_executor import SELLS, CardExecutor, ExecutionBlocked, Target
from app.execution.submit import EntriesBlocked, InvariantViolation
from app.graph.approval_graph import build_approval_graph, thread_config
from app.instruments import OptionContract
from app.ledger import repo as ledger
from app.ledger.models import Lot, LotStatus, PositionStatus, ProviderPosition
from app.ledger.positions import contract_of, state_of
from app.ledger.relink import RelinkFailed, RelinkService
from app.marketdata.port import MarketData
from app.markettime import Clock
from app.pipeline.apply import BookChanged
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName
from app.timers.models import Timer

logger = logging.getLogger(__name__)
DecisionKind = Literal["approve", "reject", "edit"]


class CardNotFound(Exception):
    pass


class DecisionConflict(Exception):
    """Not pending, stale version, or already decided -> 409."""


class DecisionInvalid(Exception):
    """Edits / missing fields / qty rules -> 422."""


class StepUpRequired(Exception):
    pass


@dataclass(frozen=True)
class _Loaded:
    card: Card
    approval: Approval | None
    position: ProviderPosition | None
    lot: Lot | None

    def target(self) -> Target | None:
        if self.position is None:
            return None
        lot_open = self.lot is not None and self.lot.status == LotStatus.OPEN
        sells_lot = self.lot is not None and self.card.action not in ENTRY_ACTIONS
        return Target(
            # 7.5: after a provider correction the lot keeps its own contract
            instrument=lot_contract(self.lot)
            if sells_lot and self.lot is not None
            else contract_of(state_of(self.position)),
            lot_id=self.lot.lot_id if self.lot else None,
            lot_qty=self.lot.qty if self.lot and lot_open else None,
        )


class ApprovalService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        checkpointer: Any,
        executor: CardExecutor,
        marketdata: MarketData,
        config: ConfigService,
        flags: FlagService,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._executor = executor
        self._md = marketdata
        self._config = config
        self._flags = flags
        self._clock = clock
        self.relink: RelinkService | None = None  # approval of re-link cards (F037)
        self._graph: CompiledStateGraph[Any, Any, Any, Any] = build_approval_graph(
            self, checkpointer
        )

    # --- graph lifecycle -------------------------------------------------------------

    async def start(self, card_id: uuid.UUID) -> None:
        """Run the card's thread up to `interrupt()`. Idempotent (F035 notify re-emission)."""
        cfg = thread_config(card_id)
        if (await self._graph.aget_state(cfg)).created_at is not None:
            return
        await self._graph.ainvoke({"card_id": str(card_id)}, cfg, durability="sync")

    async def resume(self, card_id: uuid.UUID, value: dict[str, Any]) -> None:
        """Deliver a resume value (TRD §5.1). A thread interrupted mid-run (crash after the
        resume) is continued instead; `execute` is idempotent by client order id (AC-7)."""
        await self.start(card_id)
        cfg = thread_config(card_id)
        snap = await self._graph.aget_state(cfg)
        if not snap.next:
            return  # already ended: a second resume is harmless
        command: Command[Any] | None = Command(resume=value) if snap.interrupts else None
        await self._graph.ainvoke(command, cfg, durability="sync")

    async def start_many(self, card_ids: Iterable[uuid.UUID]) -> None:
        for card_id in card_ids:
            await self._safely(self.start(card_id), card_id)

    async def invalidated(self, card_ids: Iterable[uuid.UUID], by: int | None) -> None:
        """Supersession / correction (OD-14): the card is already invalidated."""
        for card_id in card_ids:
            await self._safely(self.resume(card_id, {"decision": "invalidate", "by": by}), card_id)

    async def on_card_expiry(self, timer: Timer) -> None:
        """`card_expiry` timer (C22). A decision that won the race makes this a no-op."""
        if timer.card_id is None:
            return
        if await self._expire(timer.card_id, CardReason.TIMEOUT, CardActor.TIMER):
            await self.resume(timer.card_id, {"decision": "expire"})

    async def expire_on_restart(self) -> int:
        """TRD §9: open cards expire on restart (called by the startup sequence, F046)."""
        async with self._sessions() as db:
            ids = list(
                (
                    await db.scalars(
                        select(col(Card.card_id)).where(col(Card.state) == CardState.PENDING)
                    )
                ).all()
            )
        for card_id in ids:
            if await self._expire(card_id, CardReason.RESTART, CardActor.SYSTEM):
                await self._safely(
                    self.resume(card_id, {"decision": "expire", "reason": "restart"}), card_id
                )
        return len(ids)

    # --- T4: the user's decision -------------------------------------------------------

    async def decide(
        self,
        card_id: uuid.UUID,
        decision: DecisionKind,
        version: int,
        edits: Edits,
        *,
        session_id: uuid.UUID,
        consume_step_up: Callable[[], Awaitable[bool]],
    ) -> None:
        resume: dict[str, Any] | None = None
        async with in_transaction(self._sessions, None) as db:
            card = await cards.lock(db, card_id)
            if card is None:
                raise CardNotFound(str(card_id))
            if card.state != CardState.PENDING:
                raise DecisionConflict(f"card is {card.state}")
            if card.version != version:
                raise DecisionConflict("card changed since this version; reload")
            proposal = ProposedAction.model_validate(card.proposal)
            if decision == "edit":
                await self._edit(db, card, proposal, edits)
            elif decision == "reject":
                db.add(
                    Approval(
                        card_id=card.card_id,
                        decision=Decision.REJECT,
                        edits=edits.model_dump(mode="json", exclude_none=True),
                        card_version=card.version,
                        session_id=session_id,
                    )
                )
                await db.flush()
                await cards.transition(
                    db, card, CardState.REJECTED, CardReason.REJECTED, CardActor.USER
                )
                resume = {"decision": "reject"}
            else:
                approval = await self._approve(
                    db, card, proposal, edits, session_id, consume_step_up
                )
                resume = {"decision": "approve", "approval_id": str(approval.approval_id)}
            await notify(db, "cards", _event(card.card_id))
        if resume is not None:  # approvals are written before resuming (AC-2)
            await self._safely(self.resume(card_id, resume), card_id)

    async def _approve(
        self,
        db: AsyncSession,
        card: Card,
        proposal: ProposedAction,
        edits: Edits,
        session_id: uuid.UUID,
        consume_step_up: Callable[[], Awaitable[bool]],
    ) -> Approval:
        if extra := edits.given() - APPROVE_EDITS:
            raise DecisionInvalid(f"use an edit first for: {', '.join(sorted(extra))}")
        approved = _edited(proposal, edits)
        lot = await db.get(Lot, card.lot_id) if card.lot_id else None
        lot_qty = lot.qty if lot is not None and lot.status == LotStatus.OPEN else None
        if blockers := approval_blockers(approved, {}, lot_qty):  # AC-14, AC-15
            raise DecisionInvalid("not approvable: " + ", ".join(blockers))
        step_up = False
        if card.source == CardSource.MANUAL and card.action in ENTRY_ACTIONS:
            if not await consume_step_up():
                raise StepUpRequired()
            step_up = True
        approval = Approval(
            card_id=card.card_id,
            decision=Decision.APPROVE,
            edits=edits.model_dump(mode="json", exclude_none=True),
            approved_proposal=approved.model_dump(mode="json"),
            card_version=card.version,
            session_id=session_id,
            step_up=step_up,
            decided_at=self._clock.now(),
        )
        db.add(approval)
        await db.flush()
        await cards.transition(
            db,
            card,
            CardState.APPROVED,
            CardReason.APPROVED,
            CardActor.USER,
            detail={"approval_id": str(approval.approval_id)},
        )
        return approval

    async def _edit(
        self, db: AsyncSession, card: Card, proposal: ProposedAction, edits: Edits
    ) -> None:
        if not edits.given():
            raise DecisionInvalid("an edit needs at least one field")
        edited = _edited(proposal, edits)
        if edits.chosen_position_id is not None:
            # ponytail: the chosen position is recorded on the card; the provider link row and
            # ledger effect of a user relink are F037/F047 (relink approval path)
            position = await db.get(ProviderPosition, edits.chosen_position_id)
            if (
                position is None
                or position.provider_id != card.provider_id
                or position.status == PositionStatus.CLOSED
            ):
                raise DecisionInvalid("chosenPositionId is not an open position of this provider")
            lot = await ledger.open_lot_for_position(db, position.provider_position_id)
            card.provider_position_id = position.provider_position_id
            card.lot_id = lot.lot_id if lot else None
        completed = bool(proposal.missing_fields) and not edited.missing_fields
        await cards.transition(
            db,
            card,
            CardState.PENDING,
            CardReason.REVIEW_COMPLETED if completed else CardReason.EDITED,
            CardActor.USER,
            detail={"edits": edits.model_dump(mode="json", exclude_none=True)},
            proposal=edited.model_dump(mode="json"),
        )

    # --- approval graph steps (ApprovalSteps) --------------------------------------------

    async def revalidate(self, card_id: uuid.UUID) -> str | None:
        """C22 at approval time: fresh quote + band + entry gates for entries/adds; exits:
        position still open and qty within the lot. Failure invalidates the card."""
        loaded = await self._load(card_id)
        if loaded.approval is None or loaded.approval.approved_proposal is None:
            return "no_approval"
        proposal = ProposedAction.model_validate(loaded.approval.approved_proposal)
        reason = await self._revalidation_failure(loaded, proposal)
        if reason is not None:
            await self._fail(card_id, reason)
        return reason

    async def _revalidation_failure(self, loaded: _Loaded, p: ProposedAction) -> str | None:
        if p.action == CardAction.REVIEW:
            return None
        target = loaded.target()
        if target is None:
            return "no_contract"
        now, cfg = self._clock.now(), self._cfg()
        if p.action in ENTRY_ACTIONS:
            try:
                quote = await self._md.quote(target.instrument)
            except Exception:
                return "market_data_unavailable"  # OD-12: never guess
            if r := check_entry(p, quote, now, cfg.entry_chase_pct or Decimal(0)):
                return r.value
            flags = FlagsView(
                kill_switch=await self._flags.is_active(FlagName.KILL_SWITCH),
                entries_blocked=await self._flags.is_active(FlagName.ENTRIES_BLOCKED),
            )
            try:
                risk = await build_risk_snapshot(self._sessions, self._md, now)
            except Exception:
                logger.exception("risk snapshot failed at revalidation")
                risk = None
            option = isinstance(target.instrument, OptionContract)
            return entry_block_reason(cfg, flags, risk, p, option=option)
        assert loaded.position is not None  # noqa: S101 — target() needs it
        closed = loaded.position.status == PositionStatus.CLOSED
        if r := check_exit(p, closed, now):
            return r.value
        if p.action in SELLS | {CardAction.STOP, CardAction.TP} and target.lot_qty is None:
            return "no_open_lot"
        if p.action in SELLS and p.qty is not None and p.qty > (target.lot_qty or 0):
            return "qty_exceeds_lot"
        return None

    async def execute(self, card_id: uuid.UUID) -> list[uuid.UUID]:
        loaded = await self._load(card_id)
        approval, target = loaded.approval, loaded.target()
        assert approval is not None and approval.approved_proposal is not None  # noqa: S101
        proposal = ProposedAction.model_validate(approval.approved_proposal)
        if proposal.relink_signal_id is not None:
            await self._relink(loaded.card, proposal.relink_signal_id)
            return []
        if proposal.action == CardAction.REVIEW:
            return []  # a completed review places nothing; the card stays approved
        assert target is not None  # noqa: S101 — revalidate refused a card without one
        try:
            orders = await self._executor.execute(
                loaded.card, proposal, approval.approval_id, target
            )
        except (ExecutionBlocked, EntriesBlocked, InvariantViolation) as exc:
            await self._fail(card_id, f"execution: {exc}")
            return []
        async with in_transaction(self._sessions, None) as db:
            card = await cards.lock(db, card_id)
            if card is not None and card.state == CardState.APPROVED:
                await cards.transition(
                    db,
                    card,
                    CardState.SUBMITTED,
                    CardReason.SUBMITTED,
                    CardActor.SYSTEM,
                    detail={"order_ids": [str(o.order_id) for o in orders]},
                )
                await notify(db, "cards", _event(card_id))
        return [o.order_id for o in orders]

    async def _relink(self, card: Card, signal_id: uuid.UUID) -> None:
        """F037: the link, ledger change and follow-up cards; places no order itself."""
        assert self.relink is not None  # noqa: S101 — wired whenever re-link cards exist
        try:
            created, superseded = await self.relink.apply_approved(card, signal_id)
        except (RelinkFailed, BookChanged) as exc:
            await self._fail(card.card_id, f"relink: {exc}")
            return
        await self.invalidated(superseded, None)
        await self.start_many(created)

    # --- helpers ---------------------------------------------------------------------

    async def _load(self, card_id: uuid.UUID) -> _Loaded:
        async with self._sessions() as db:
            card = await db.get(Card, card_id)
            if card is None:
                raise CardNotFound(str(card_id))
            approval = await db.scalar(select(Approval).where(col(Approval.card_id) == card_id))
            position = (
                await db.get(ProviderPosition, card.provider_position_id)
                if card.provider_position_id
                else None
            )
            lot = await db.get(Lot, card.lot_id) if card.lot_id else None
            if lot is None and position is not None and card.action not in ENTRY_ACTIONS:
                lot = await ledger.open_lot_for_position(db, position.provider_position_id)
        return _Loaded(card, approval, position, lot)

    async def _fail(self, card_id: uuid.UUID, reason: str) -> None:
        async with in_transaction(self._sessions, None) as db:
            card = await cards.lock(db, card_id)
            if card is None or card.state != CardState.APPROVED:
                return
            await cards.transition(
                db,
                card,
                CardState.INVALIDATED,
                CardReason.REVALIDATE_FAILED,
                CardActor.SYSTEM,
                detail={"reason": reason},
            )
            await notify(db, "cards", _event(card_id))

    async def _expire(self, card_id: uuid.UUID, reason: CardReason, actor: CardActor) -> bool:
        async with in_transaction(self._sessions, None) as db:
            card = await cards.lock(db, card_id)
            if card is None:
                return False
            try:
                await cards.transition(db, card, CardState.EXPIRED, reason, actor)
            except IllegalTransition:
                return False  # decided / invalidated first
            await notify(db, "cards", _event(card_id))
        return True

    async def _safely(self, work: Awaitable[None], card_id: uuid.UUID) -> None:
        """The decision is committed; a failing graph run must not undo or hide it. The card
        stays where the run stopped and the startup sequence recovers it (F046)."""
        try:
            await work
        except Exception:
            logger.exception("approval graph run failed", extra={"card_id": str(card_id)})

    def _cfg(self) -> RuntimeConfig:
        return self._config.get() or RuntimeConfig()


def _edited(proposal: ProposedAction, edits: Edits) -> ProposedAction:
    try:
        return apply_edits(proposal, edits)
    except EditInvalid as exc:
        raise DecisionInvalid(str(exc)) from None


def _event(card_id: uuid.UUID) -> str:
    return json.dumps({"type": "card.updated", "id": str(card_id)})
