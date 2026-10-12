"""Human in the loop for every card (F036, C14, PRD G3 / §9 / 7.1 step 5 / 7.2).

`decide` is transaction T4: lock the card, check `pending` + `version`, write the `approvals`
row and the transition, commit, THEN resume the card's approval graph. Timer expiry,
supersession and restart resume the same graph. Approved cards are revalidated (C22, entry
gates) before `CardExecutor` submits through `submit_order()`.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.broker.port import BrokerClient
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
    CardType,
    Decision,
)
from app.cards.preflight import Check, Preflight, Status
from app.cards.state import IllegalTransition
from app.cards.validity import (
    ENTRY_ACTIONS,
    SUPERSEDING,
    approval_blockers,
    check_entry,
    check_exit,
)
from app.db import in_transaction, notify
from app.decide.risk import entry_block_reason
from app.decide.risk_snapshot import build_risk_snapshot, lot_contract
from app.decide.types import FlagsView, ProposedAction
from app.dispatcher.types import ProcessingContext
from app.execution.card_executor import SELLS, CardExecutor, ExecutionBlocked, Target
from app.execution.models import OrderStatus
from app.execution.submit import (
    EntriesBlocked,
    InvariantViolation,
    ModeMismatch,
    OutsideRegularHours,
)
from app.graph.approval_graph import build_approval_graph, thread_config
from app.inbox.models import MessageEvent
from app.inbox.repo import to_envelope_row
from app.instruments import Instrument, OptionContract, broker_symbol
from app.interpret.models import Signal
from app.interpret.signal import F, ResolvedSource
from app.ledger import repo as ledger
from app.ledger.lots import OPTION_MULTIPLIER
from app.ledger.models import Lot, LotStatus, PositionStatus, ProviderPosition
from app.ledger.positions import PositionUpdate, contract_of, state_of
from app.ledger.relink import RelinkFailed, RelinkService
from app.logging import bind
from app.marketdata.freshness import is_stale
from app.marketdata.port import MarketData
from app.markettime import Clock, is_regular_hours
from app.pipeline.apply import BookChanged, position_id_for, stored_signal
from app.pipeline.plan import Planner
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName
from app.timers.models import Timer

logger = logging.getLogger(__name__)
UNREADABLE = "broker_position_unreadable"  # not a verdict on the quantity: left to revalidation
DecisionKind = Literal["approve", "reject", "edit"]
QTY_ACTIONS = ENTRY_ACTIONS | {CardAction.TRIM, CardAction.EXIT}  # the user types the quantity
QTY_CHECKS = frozenset({"risk", "lot", "broker_position"})  # preflight checks the quantity moves


class CardNotFound(Exception):
    pass


class DecisionConflict(Exception):
    """Not pending, stale version, or already decided -> 409."""


class DecisionInvalid(Exception):
    """Edits / missing fields / qty rules -> 422."""


@dataclass(frozen=True)
class _Loaded:
    card: Card
    approval: Approval | None
    position: ProviderPosition | None
    lot: Lot | None

    def target(self, proposal: ProposedAction | None = None) -> Target | None:
        lot_open = self.lot is not None and self.lot.status == LotStatus.OPEN
        if self.lot is not None and self.card.action not in ENTRY_ACTIONS:
            instrument = lot_contract(self.lot)  # 7.5: after a provider correction the lot keeps
        elif self.position is not None:  # its own contract; foreign lots have no position (C47)
            instrument = contract_of(state_of(self.position))
        elif (
            self.approval is not None and self.approval.approved_proposal is not None
        ) or proposal is not None:
            approved = (
                ProposedAction.model_validate(self.approval.approved_proposal)
                if self.approval is not None and self.approval.approved_proposal is not None
                else proposal
            )
            contract = approved.contract if approved else None
            if contract is None:  # a manual entry names its own contract (F047)
                return None
            instrument = contract
        else:
            return None
        return Target(
            instrument=instrument,
            lot_id=self.lot.lot_id if self.lot else None,
            lot_qty=self.lot.qty if self.lot and lot_open else None,
        )


@dataclass(frozen=True)
class _Confirmed:
    """AC-14: the card re-planned with the user's confirmed underlying (built outside the
    card lock: it fetches market data)."""

    proposal: ProposedAction
    opening: PositionUpdate | None  # the provider position the pipeline could not open
    position_id: uuid.UUID
    channel_id: int


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
        broker: Callable[[], BrokerClient] | None = None,  # the broker of the trading mode
        account: Callable[[], BrokerClient | None] | None = None,  # None result: shadow mode
    ) -> None:
        self._broker = broker
        self._account = account
        self._sessions = sessions
        self._executor = executor
        self._md = marketdata
        self._config = config
        self._flags = flags
        self._clock = clock
        self.relink: RelinkService | None = None  # approval of re-link cards (F037)
        self.planner: Planner | None = None  # AC-14: re-plan on underlying confirmation
        self.unblock_poll_s = 5.0
        self._deferred: asyncio.Task[None] | None = None
        # F076: one account. The last risk check before an entry's submission and the
        # submission itself (which commits the order row the next check counts as working
        # exposure) run one entry at a time, so two approvals cannot both pass on the same
        # snapshot. ponytail: in-process lock (one app process, `app.system.singleton`);
        # pg_advisory_xact_lock around check + order insert if the app ever scales out.
        self._entry_gate = asyncio.Lock()
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

    async def resume_approved(self, *, entries: bool = True) -> int:
        """TRD §9: an approved card whose thread stopped after the decision (crash before or
        during `execute`) continues; `execute` is idempotent by client order id (AC-7). The
        approval is already committed, so an unresolved interrupt gets it, not an expiry.
        `entries=False` leaves approved entries untouched (still `approved`): revalidating
        them while entries are blocked would invalidate the approval."""
        query = (
            select(col(Card.card_id), col(Approval.approval_id))
            .join(Approval, col(Approval.card_id) == col(Card.card_id))
            .where(col(Card.state) == CardState.APPROVED)
        )
        if not entries:
            query = query.where(col(Card.action).not_in(ENTRY_ACTIONS))
        async with self._sessions() as db:
            rows = (await db.execute(query)).all()
        resumed = 0
        for card_id, approval_id in rows:
            snap = await self._graph.aget_state(thread_config(card_id))
            if snap.created_at is not None and not snap.next:
                continue  # the run finished (e.g. a completed review stays approved)
            resumed += 1
            await self._safely(
                self.resume(card_id, {"decision": "approve", "approval_id": str(approval_id)}),
                card_id,
            )
        return resumed

    def resume_when_entries_unblocked(self) -> None:
        """After a dirty reconcile: resume the approved entries once `entries_blocked` is
        lifted (a later clean reconciliation or a manual fix). Idempotent."""
        if self._deferred is None or self._deferred.done():
            self._deferred = asyncio.create_task(
                self._resume_after_block(), name="resume-approved-entries"
            )

    async def _resume_after_block(self) -> None:
        while True:
            await asyncio.sleep(self.unblock_poll_s)
            try:
                if await self._flags.is_active(FlagName.ENTRIES_BLOCKED):
                    continue
                resumed = await self.resume_approved()
            except Exception:
                logger.exception("resuming approved entries failed; retrying")
                continue
            logger.info("approved entries resumed after the block", extra={"resumed": resumed})
            return

    async def stop(self) -> None:
        if self._deferred is not None:
            self._deferred.cancel()
            await asyncio.gather(self._deferred, return_exceptions=True)

    # --- T4: the user's decision -------------------------------------------------------

    async def decide(
        self,
        card_id: uuid.UUID,
        decision: DecisionKind,
        version: int,
        edits: Edits,
        *,
        session_id: uuid.UUID,
    ) -> None:
        with bind(card_id=str(card_id)):
            resume: dict[str, Any] | None = None
            confirmed = None
            if decision == "edit" and edits.confirmed_underlying is not None:
                confirmed = await self._confirmed_plan(card_id, edits.confirmed_underlying)
            if decision == "approve":
                await self._qty_gate(
                    card_id, version, edits
                )  # outside the card lock: it reads market data
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
                    await self._edit(db, card, proposal, edits, confirmed)
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
                    approval = await self._approve(db, card, proposal, edits, session_id)
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
    ) -> Approval:
        if extra := edits.given() - APPROVE_EDITS:
            raise DecisionInvalid(f"use an edit first for: {', '.join(sorted(extra))}")
        approved = _approve_qty(card, proposal, edits)
        lot = await db.get(Lot, card.lot_id) if card.lot_id else None
        lot_qty = lot.qty if lot is not None and lot.status == LotStatus.OPEN else None
        if approved.action == CardAction.CLOSE:  # the dedicated full close: what the lot holds now
            approved = approved.model_copy(update={"qty": lot_qty})
        if blockers := approval_blockers(approved, {}, lot_qty):  # AC-14, AC-15
            raise DecisionInvalid("not approvable: " + ", ".join(blockers))
        approval = Approval(
            card_id=card.card_id,
            decision=Decision.APPROVE,
            edits=edits.model_dump(mode="json", exclude_none=True),
            approved_proposal=approved.model_dump(mode="json"),
            card_version=card.version,
            session_id=session_id,
            step_up=False,  # password step-up removed 2026-10-12: the session authorizes
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
        self,
        db: AsyncSession,
        card: Card,
        proposal: ProposedAction,
        edits: Edits,
        confirmed: _Confirmed | None = None,
    ) -> None:
        if not edits.given():
            raise DecisionInvalid("an edit needs at least one field")
        edited = _edited(proposal, edits)  # checks the confirmation against the suggestion
        if confirmed is not None and "underlying_confirmation" in proposal.missing_fields:
            # AC-14: the card becomes the confirmed signal's card: contract, size, legs. Its
            # validity window stays the one its expiry timer was scheduled with.
            replanned = confirmed.proposal.model_copy(update={"valid_until": proposal.valid_until})
            rest = edits.model_copy(update={"confirmed_underlying": None})
            edited = _edited(replanned, rest) if rest.given() else replanned
            card.card_type = edited.card_type
            if confirmed.opening is not None and card.provider_id is not None:
                if await db.get(ProviderPosition, confirmed.position_id) is None:
                    await ledger.record(
                        db,
                        provider_id=card.provider_id,
                        row=None,
                        update=confirmed.opening,
                        signal_id=card.signal_id,
                        channel_id=confirmed.channel_id,
                        position_id=confirmed.position_id,
                        at=self._clock.now(),
                    )
                card.provider_position_id = confirmed.position_id
        if edits.chosen_position_id is not None:
            position = await db.get(ProviderPosition, edits.chosen_position_id)
            offered = proposal.candidate_position_ids
            if (
                position is None
                or position.provider_id != card.provider_id
                or position.status == PositionStatus.CLOSED
                or (offered and position.provider_position_id not in offered)
                or position.opened_channel_id != await _signal_channel(db, card.signal_id)
            ):
                raise DecisionInvalid("chosenPositionId is not an open position of this lane")
            lot = await ledger.open_lot_for_position(db, position.provider_position_id)
            card.provider_position_id = position.provider_position_id
            card.lot_id = lot.lot_id if lot else None
            if card.signal_id is not None and "chosen_position_id" in proposal.missing_fields:
                # F075: the person resolved the ambiguity. Approving this card writes it like a
                # re-link (RelinkService.apply_approved: a user_correction link row, the
                # provider-ledger change, then a fresh card for the action), so later messages
                # see the corrected state. Approving places no order itself.
                edited = edited.model_copy(
                    update={
                        "action": CardAction.REVIEW,
                        "card_type": CardType.REVIEW,
                        "relink_signal_id": card.signal_id,
                        "candidate_position_ids": [position.provider_position_id],
                        "missing_fields": [],
                        "review_reasons": [*edited.review_reasons, "position_chosen"],
                    }
                )
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

    async def _qty_gate(self, card_id: uuid.UUID, version: int, edits: Edits) -> None:
        """C105: the quantity the user typed is checked against the position, the broker and the
        risk limits BEFORE it is approved; a violation blocks with its reason and the number is
        never reduced. Card state / version conflicts are left to the transaction that follows.
        Manual cards (the form already took the quantity) and closes skip it."""
        loaded = await self._load(card_id)
        card = loaded.card
        if card.state != CardState.PENDING or card.version != version:
            return  # the transaction below answers with the conflict
        if card.source == CardSource.MANUAL:
            return
        proposal = ProposedAction.model_validate(card.proposal)
        if proposal.action not in QTY_ACTIONS or edits.qty is None:
            return  # a missing quantity is refused by `_approve`
        approved = _edited(proposal, edits)
        pf = await self._preflight(loaded, approved, stop_on_fail=False)
        for check in pf.checks:
            if (
                check.name in QTY_CHECKS
                and check.status == "blocked"
                and check.reason != UNREADABLE
            ):
                raise DecisionInvalid(f"quantity not allowed: {check.reason or check.name}")

    # --- approval graph steps (ApprovalSteps) --------------------------------------------

    async def revalidate(self, card_id: uuid.UUID) -> str | None:
        """C22 at approval time, from fresh data (the approved card is never trusted as still
        valid): hours, validity, quote + freshness + band, supersession, flags, account, working
        orders, positions and risk limits for entries/adds; position, lot qty and broker
        position for exits. Failure invalidates the card."""
        loaded = await self._load(card_id)
        if loaded.approval is None or loaded.approval.approved_proposal is None:
            return "no_approval"
        proposal = ProposedAction.model_validate(loaded.approval.approved_proposal)
        reason = (await self._preflight(loaded, proposal, stop_on_fail=True)).failure
        if reason is not None:
            await self._fail(card_id, reason)
        return reason

    async def preflight(self, card_id: uuid.UUID, qty: int | None = None) -> Preflight:
        """The revalidation checks, read-only, for a card the user is looking at (`qty`: what
        they typed). Changes nothing; the decision itself is still revalidated after approval."""
        loaded = await self._load(card_id)
        card = loaded.card
        if card.state not in (CardState.PENDING, CardState.APPROVED):
            return Preflight(applicable=False)
        raw = (
            loaded.approval.approved_proposal
            if loaded.approval and loaded.approval.approved_proposal
            else card.proposal
        )
        proposal = ProposedAction.model_validate(raw)
        if qty is not None:
            proposal = proposal.model_copy(update={"qty": qty})
        return await self._preflight(loaded, proposal, stop_on_fail=False)

    async def _preflight(
        self, loaded: _Loaded, p: ProposedAction, *, stop_on_fail: bool
    ) -> Preflight:
        out = Preflight(
            applicable=p.action != CardAction.REVIEW,
            qty=p.qty,
            signal_price=p.signal_price,
            limit_price=p.limit_price,
        )
        if not out.applicable:
            return out

        def add(
            name: str, status: Status, reason: str | None = None, detail: str | None = None
        ) -> bool:
            out.checks.append(Check(name, status, reason, detail))
            return status == "pass" or not stop_on_fail  # False = stop here

        now, cfg = self._clock.now(), self._cfg()
        if not add(
            "hours",
            "pass" if is_regular_hours(now) else "blocked",
            None if is_regular_hours(now) else "outside_regular_hours",  # 7.7
        ):
            return out
        target = loaded.target(p)
        if target is None:
            add("contract", "blocked", "no_contract")
            return out
        entry = p.action in ENTRY_ACTIONS
        lapsed = p.valid_until is not None and now > p.valid_until
        if not add(
            "validity",
            "blocked" if lapsed else "pass",
            CardReason.TIMEOUT.value if lapsed else None,
        ):
            return out
        if not entry:
            await self._exit_checks(out, add, loaded, p, target, now)
            return out

        quote = None
        try:
            quote = await self._md.quote(target.instrument)
        except Exception:
            logger.warning("no quote at revalidation", exc_info=True)
            if not add("quote", "blocked", "market_data_unavailable"):  # OD-12: never guess
                return out
        if quote is not None:
            out.bid, out.ask, out.last, out.quote_at = quote.bid, quote.ask, quote.last, quote.at
            add("quote", "pass")
            old = is_stale(quote, now, cfg.quote_max_age_s)
            if not add(
                "quote_fresh",
                "blocked" if old else "pass",
                "quote_stale" if old else None,
                f"quote from {quote.at:%H:%M:%S}" if old else None,
            ):
                return out
            band = check_entry(p, quote, now, cfg.entry_chase_pct or Decimal(0))
            if band is not None and band != CardReason.TIMEOUT:
                if not add("price_band", "blocked", band.value):
                    return out
            else:
                add("price_band", "pass")
        if not add("superseded", *await self._superseded(loaded)):
            return out
        await self._risk_checks(out, add, loaded, p, target, now)
        return out

    async def _superseded(self, loaded: _Loaded) -> tuple[Status, str | None]:
        card = loaded.card
        if card.provider_id is None:
            return "pass", None  # a manual card has no provider message to be overtaken by
        async with self._sessions() as db:
            why = await ledger.superseded_after_approval(
                db,
                provider_id=card.provider_id,
                position_id=card.provider_position_id,
                signal_id=card.signal_id,
                posted_at=card.posted_at,
                card_created_at=card.created_at,
                superseding=SUPERSEDING,
            )
        return ("blocked", why) if why else ("pass", None)

    async def _risk_checks(
        self,
        out: Preflight,
        add: Callable[..., bool],
        loaded: _Loaded,
        p: ProposedAction,
        target: Target,
        now: datetime,
    ) -> None:
        cfg = self._cfg()
        flags = FlagsView(
            kill_switch=await self._flags.is_active(FlagName.KILL_SWITCH),
            entries_blocked=await self._flags.is_active(FlagName.ENTRIES_BLOCKED),
        )
        try:
            risk = await build_risk_snapshot(
                self._sessions,
                self._md,
                now,
                broker=self._account() if self._account else None,
                max_quote_age_s=cfg.quote_max_age_s,
                exclude_card_id=loaded.card.card_id,
            )
        except Exception:
            logger.exception("risk snapshot failed at revalidation")
            risk = None
        option = isinstance(target.instrument, OptionContract)
        if p.qty is not None and p.limit_price is not None:
            out.est_cost = p.qty * p.limit_price * (OPTION_MULTIPLIER if option else 1)
        out.max_exposure = cfg.max_total_exposure_usd
        out.max_open_positions = cfg.max_open_positions
        out.max_daily_loss = cfg.max_daily_loss_usd
        if risk is not None:
            out.equity, out.cash, out.buying_power = risk.equity, risk.cash, risk.buying_power
            available = (
                risk.options_buying_power
                if option and risk.options_buying_power is not None
                else risk.buying_power
            )
            if available is not None and out.est_cost is not None:
                out.projected_buying_power = available - out.est_cost
            out.exposure, out.working_exposure = risk.exposure, risk.working_exposure
            if risk.exposure is not None and out.est_cost is not None:
                out.projected_exposure = risk.exposure + out.est_cost
            out.open_positions = risk.open_positions
            if risk.realized_today is not None and risk.unrealized is not None:
                out.daily_pnl = risk.realized_today + risk.unrealized
        reason = entry_block_reason(cfg, flags, risk, p, option=option)
        unknown = reason is not None and reason.startswith(("risk unknown", "config incomplete"))
        add(
            "risk",
            "pass" if reason is None else "review" if unknown else "blocked",
            reason,
            reason,
        )

    async def _exit_checks(
        self,
        out: Preflight,
        add: Callable[..., bool],
        loaded: _Loaded,
        p: ProposedAction,
        target: Target,
        now: datetime,
    ) -> None:
        # the provider closing P is what an "all out" card is about: what dies is the USER's lot
        gone = check_exit(p, target.lot_qty is None, now)
        if not add("position", "blocked" if gone else "pass", gone.value if gone else None):
            return
        needs_lot = p.action in SELLS | {CardAction.STOP, CardAction.TP}
        if needs_lot and target.lot_qty is None:
            add("lot", "blocked", "no_open_lot")
            return
        if p.action in SELLS and p.qty is not None and p.qty > (target.lot_qty or 0):
            add("lot", "blocked", "qty_exceeds_lot")
            return
        if p.action == CardAction.CLOSE and p.qty != target.lot_qty:
            add("lot", "blocked", "lot_qty_changed")  # C105: the user approved another lot size
            return
        add("lot", "pass")
        if p.action in SELLS and p.qty is not None and self._broker is not None:
            held = await self._broker_qty(target.instrument)
            short = held is None or held < p.qty
            add(
                "broker_position",
                "blocked" if short else "pass",
                None if not short else "broker_position_short" if held is not None else UNREADABLE,
            )

    async def _broker_qty(self, instrument: Instrument) -> int | None:
        """The broker's own holding of this symbol, fresh; None = unreadable (refuse the sell)."""
        assert self._broker is not None  # noqa: S101 — callers check
        try:
            positions = await self._broker().positions()
        except Exception:
            logger.exception("broker positions unavailable at revalidation")
            return None
        symbol = broker_symbol(instrument)
        return sum(pos.qty for pos in positions if broker_symbol(pos.instrument) == symbol)

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
            if proposal.action in ENTRY_ACTIONS:
                async with self._entry_gate:
                    # revalidation ran outside the gate: another entry may have been submitted
                    # since. Check again against what is committed now, then submit.
                    final = await self._preflight(loaded, proposal, stop_on_fail=True)
                    if final.failure is not None:
                        await self._fail(card_id, final.failure)
                        return []
                    orders = await self._executor.execute(
                        loaded.card, proposal, approval.approval_id, target
                    )
            else:
                orders = await self._executor.execute(
                    loaded.card, proposal, approval.approval_id, target
                )
        except (
            ExecutionBlocked,
            EntriesBlocked,
            InvariantViolation,
            ModeMismatch,
            OutsideRegularHours,
        ) as exc:
            await self._fail(card_id, f"execution: {exc}")
            return []
        if orders and all(o.status == OrderStatus.REJECTED for o in orders):
            # the broker refused it: the card says so instead of claiming a submission
            await self._fail(
                card_id,
                "order_rejected",
                code=CardReason.ORDER_REJECTED,
                extra={"order_ids": [str(o.order_id) for o in orders]},
            )
            return [o.order_id for o in orders]
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
            assert card.provider_id is not None and card.provider_position_id is not None  # noqa: S101
            created, superseded = await self.relink.apply_approved(
                card.provider_position_id, signal_id
            )
        except (RelinkFailed, BookChanged) as exc:
            await self._fail(card.card_id, f"relink: {exc}")
            return
        await self.invalidated(superseded, None)
        await self.start_many(created)

    async def _confirmed_plan(self, card_id: uuid.UUID, underlying: str) -> _Confirmed | None:
        """The entry signal validated and decided again with the confirmed underlying; None
        when there is nothing to re-plan (not an entry from a signal, no planner wired)."""
        if self.planner is None:
            return None
        async with self._sessions() as db:
            card = await db.get(Card, card_id)
            if card is None or card.action != CardAction.ENTRY or card.signal_id is None:
                return None
            sig = await db.get(Signal, card.signal_id)
            event = (
                await db.get(MessageEvent, (sig.message_id, sig.event_type, sig.event_seq))
                if sig is not None
                else None
            )
        if sig is None or event is None:
            return None
        parsed = stored_signal(sig)
        value = F[str](value=underlying, source=ResolvedSource(rule="user_confirmed_underlying"))
        parsed = parsed.model_copy(
            update={"fields": parsed.fields.model_copy(update={"underlying": value})}
        )
        now, env = self._clock.now(), to_envelope_row(event)
        ctx = ProcessingContext(
            envelope=env, attempt=1, started_at=now, age_s=0, stale=False, late=False
        )
        proposals, opening = await self.planner.confirm_underlying(
            ctx, parsed, None, self._cfg(), now=now
        )
        entry = next((p for p in proposals if p.action == CardAction.ENTRY), None)
        if entry is None:
            return None
        return _Confirmed(entry, opening, position_id_for(sig.signal_id), env.channel_id)

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

    async def _fail(
        self,
        card_id: uuid.UUID,
        reason: str,
        *,
        code: CardReason = CardReason.REVALIDATE_FAILED,
        extra: dict[str, Any] | None = None,
    ) -> None:
        async with in_transaction(self._sessions, None) as db:
            card = await cards.lock(db, card_id)
            if card is None or card.state != CardState.APPROVED:
                return
            await cards.transition(
                db,
                card,
                CardState.INVALIDATED,
                code,
                CardActor.SYSTEM,
                detail={"reason": reason, **(extra or {})},
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


def _approve_qty(card: Card, proposal: ProposedAction, edits: Edits) -> ProposedAction:
    """The proposal as approved. C105: for a buy or sell the quantity is the one the user sent
    with this approval, never the card's own; a close takes no quantity (it sells the lot)."""
    if _unreadable(proposal):
        raise DecisionInvalid("nothing to approve: this signal could not be read; dismiss it")
    if proposal.action == CardAction.CLOSE:
        if edits.qty is not None:
            raise DecisionInvalid("a close sells the whole position: no quantity")
    elif proposal.action in QTY_ACTIONS and card.source != CardSource.MANUAL:
        if edits.qty is None:
            raise DecisionInvalid("qty required: enter the number of shares or contracts")
    return _edited(proposal, edits)


def _unreadable(proposal: ProposedAction) -> bool:
    """A review card for a signal the system could not read (raw review, unknown correction
    target): it holds no trade and no re-link, so approving it would only look like a trade
    was accepted. Re-link and position-choice cards carry a signal / candidate and stay."""
    return (
        proposal.action == CardAction.REVIEW
        and proposal.relink_signal_id is None
        and not proposal.candidate_position_ids
    )


def _edited(proposal: ProposedAction, edits: Edits) -> ProposedAction:
    try:
        return apply_edits(proposal, edits)
    except EditInvalid as exc:
        raise DecisionInvalid(str(exc)) from None


def _event(card_id: uuid.UUID) -> str:
    return json.dumps({"type": "card.updated", "id": str(card_id)})


async def _signal_channel(db: AsyncSession, signal_id: uuid.UUID | None) -> int | None:
    """The lane channel of the card's message (F068); None for a card without a signal."""
    if signal_id is None:
        return None
    channel: int | None = await db.scalar(
        select(col(MessageEvent.channel_id))
        .join(Signal, col(Signal.message_id) == col(MessageEvent.message_id))
        .where(col(Signal.signal_id) == signal_id)
        .limit(1)
    )
    return channel
