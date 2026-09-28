"""Deterministic pipeline for one message event (M2; the message graph replaces it in F035):
context -> interpret -> literal spans -> validate -> link -> ledger effect -> decide, all
before the transaction, then `apply_event` in ONE transaction under the provider slot.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.cards.models import CardAction, CardType
from app.db import in_transaction
from app.decide.engine import decide
from app.decide.risk_snapshot import build_risk_snapshot
from app.decide.types import DecisionInputs, FlagsView, LotView, ProposedAction, RiskSnapshot
from app.dispatcher.types import ProcessingContext
from app.inbox import repo as inbox
from app.inbox.models import EventType, InboxStatus
from app.interpret import context_repo
from app.interpret.candidates import find_candidates
from app.interpret.context import load_context
from app.interpret.interpreter import Interpreter, InterpreterUnavailable
from app.interpret.models import Interpreter as InterpreterKind
from app.interpret.models import LinkMethod, LinkOutcome, MsgType
from app.interpret.signal import ParsedSignal, ParsedSignals
from app.interpret.spans import verify_literal_spans
from app.ledger import repo as ledger
from app.ledger.correlation import LinkInputs, ParentLink, resolve_link
from app.ledger.positions import (
    OPENING,
    InvalidTransition,
    PositionState,
    PositionUpdate,
    apply_to_position,
    contract_of,
    state_of,
)
from app.marketdata.port import MarketData
from app.markettime import Clock
from app.pipeline.apply import (
    ApplyInput,
    SignalOutcome,
    apply_event,
    apply_raw_review,
    position_id_for,
    signal_id_for,
)
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName
from app.validate.normalize import (
    NeedsReview,
    NormalizedSignal,
    mid_price,
    premium_prices,
    scale_prices,
)
from app.validate.service import ValidationResult, ValidationService

logger = logging.getLogger(__name__)
SIZED_TYPES = OPENING | {MsgType.ADD}
Book = dict[uuid.UUID, PositionState]


class PipelineProcessor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        config: ConfigService,
        interpreter: Interpreter,
        validation: ValidationService,
        marketdata: MarketData,
        flags: FlagService,
        alerts: AlertService,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._config = config
        self._interpreter = interpreter
        self._validation = validation
        self._md = marketdata
        self._flags = flags
        self._alerts = alerts
        self._clock = clock

    async def process(self, ctx: ProcessingContext) -> None:
        env = ctx.envelope
        config = self._config.get() or RuntimeConfig()
        if env.key.event_type != EventType.CREATE:
            # ponytail: edits/deletes stay feed-only until corrections land (F038)
            async with in_transaction(self._sessions, None) as db:
                await inbox.mark(db, env.key, InboxStatus.DONE, late=ctx.late)
            return
        now = self._clock.now()
        async with self._sessions() as db:
            context = await load_context(
                db, env, recent_n=config.context_recent_messages or 0, now=now
            )
            rows = await context_repo.open_book(db, env.provider_id)
            parents = await ledger.parent_links(db, env.reply_to_id) if env.reply_to_id else ()
        try:
            parsed = await self._interpreter.interpret(context, find_candidates(env.text or ""))
        except InterpreterUnavailable as exc:
            await self._raw_review(ctx, "parser_degraded", alert=str(exc))
            return
        if verify_literal_spans(parsed, env.text or ""):
            await self._raw_review(ctx, "literal_span_mismatch", alert=None)  # AC-11
            return

        book: Book = {r.provider_position_id: state_of(r) for r in rows}
        version = {r.provider_position_id: r.updated_at for r in rows}
        outcomes = await self._plan(ctx, parsed, config, book, parents, now)
        kind = InterpreterKind.NONE if parsed.interpreter == "stub" else parsed.interpreter
        async with in_transaction(self._sessions, None) as db:
            await apply_event(db, ApplyInput(ctx, InterpreterKind(kind), version, outcomes))

    async def _plan(
        self,
        ctx: ProcessingContext,
        parsed: ParsedSignals,
        config: RuntimeConfig,
        book: Book,
        parents: tuple[ParentLink, ...],
        now: datetime,
    ) -> list[SignalOutcome]:
        env = ctx.envelope
        method = LinkMethod.JEV if parsed.interpreter == "jev" else LinkMethod.LLM
        flags = FlagsView(
            kill_switch=await self._flags.is_active(FlagName.KILL_SWITCH),
            entries_blocked=await self._flags.is_active(FlagName.ENTRIES_BLOCKED),
        )
        risk = None
        if any(s.msg_type in SIZED_TYPES for s in parsed.signals):
            risk = await self._risk(now)

        outcomes = []
        for idx, sig in enumerate(parsed.signals):  # in order: later signals see earlier effects
            signal_id = signal_id_for(env.key, idx)
            v = await self._validation.validate(sig, env, config=config, now=now)
            norm = v.normalized
            link = None
            if norm is not None:
                link = resolve_link(
                    norm,
                    sig.link_hint,
                    LinkInputs(env.provider_id, dict(book), parents),
                    t_link=config.t_link,
                    hint_method=method,
                )
            position_id = position = None
            if link is not None and link.outcome == LinkOutcome.LINKED:
                position_id = link.position_id
                position = book[position_id] if position_id else None
            elif link is not None and link.outcome == LinkOutcome.NEW_POSITION:
                position_id = position_id_for(signal_id)
            if position is not None and position.instrument == "option" and norm is not None:
                v = await self._scale_follow_up(v, norm, position, config)
                norm = v.normalized

            update: PositionUpdate | None = None
            ledger_error = None
            if position_id is not None and v.status == "ok" and norm is not None:
                try:
                    update = apply_to_position(position, norm)
                except InvalidTransition as exc:
                    ledger_error = str(exc)

            lot = await self._lot(position_id) if position is not None else None
            proposals = decide(
                DecisionInputs(
                    signal=sig,
                    validation=v,
                    link=link,
                    position=position,
                    lot=lot,
                    config=config,
                    tick=await self._tick(sig, v, position, norm),
                    posted_at=env.posted_at,
                    stale=ctx.stale,
                    late=ctx.late,
                    flags=flags,
                    risk=risk,
                )
            )
            if ledger_error is not None:  # F020: an illegal ledger change is never applied
                proposals = [
                    ProposedAction(
                        action=proposals[0].action if proposals else CardAction.REVIEW,
                        card_type=CardType.REVIEW,
                        review_reasons=[f"ledger:{ledger_error}"],
                    )
                ]
            if update is not None and position_id is not None:
                if update.after.status == "closed":
                    book.pop(position_id, None)
                else:
                    book[position_id] = update.after
            outcomes.append(
                SignalOutcome(
                    idx=idx,
                    signal_id=signal_id,
                    parsed=sig,
                    link=link,
                    position_id=position_id,
                    update=update,
                    lot_id=lot.lot_id if lot else None,
                    cards=proposals,
                )
            )
        return outcomes

    async def _scale_follow_up(
        self,
        v: ValidationResult,
        norm: NormalizedSignal,
        position: PositionState,
        config: RuntimeConfig,
    ) -> ValidationResult:
        """Follow-up premiums were left unscaled (no contract then): scale them now against
        the linked contract's live quote (FR-12). Unavailable quote -> review, never a guess."""
        if v.status != "ok" or all(p.scale is not None for p in premium_prices(norm)):
            return v
        try:
            quote = await self._md.quote(contract_of(position))
        except Exception:
            logger.warning("no quote to scale follow-up prices")
            return v.model_copy(update={"status": "review", "reasons": ["market_data_unavailable"]})
        scaled = scale_prices(
            norm.model_copy(update={"instrument": "option"}),
            mid=mid_price(quote),
            tolerance_pct=config.identity_price_tolerance_pct,
        )
        if isinstance(scaled, NeedsReview):
            return v.model_copy(update={"status": "review", "reasons": list(scaled.reasons)})
        return v.model_copy(update={"normalized": scaled})

    async def _lot(self, position_id: uuid.UUID | None) -> LotView | None:
        if position_id is None:
            return None
        async with self._sessions() as db:
            row = await ledger.open_lot_for_position(db, position_id)
        return LotView(row.lot_id, row.qty, row.avg_price) if row else None

    async def _tick(
        self,
        sig: ParsedSignal,
        v: ValidationResult,
        position: PositionState | None,
        norm: NormalizedSignal | None,
    ) -> Decimal | None:
        if sig.msg_type not in SIZED_TYPES or norm is None:
            return None
        price = norm.entry_price or norm.entry_price_high or norm.entry_price_low
        contract = v.contract or (contract_of(position) if position else None)
        if price is None or contract is None:
            return None
        try:
            return await self._md.tick_size(contract, price.value)
        except Exception:
            logger.warning("tick size unavailable")
            return None  # decide -> review "tick_unknown" (OD-25)

    async def _risk(self, now: datetime) -> RiskSnapshot | None:
        try:
            return await build_risk_snapshot(self._sessions, self._md, now)
        except Exception:
            logger.exception("risk snapshot failed")
            return None  # entries disabled "risk unknown"

    async def _raw_review(self, ctx: ProcessingContext, reason: str, alert: str | None) -> None:
        env = ctx.envelope
        async with in_transaction(self._sessions, None) as db:
            card_id = await apply_raw_review(db, ctx, reason, mark=InboxStatus.DONE)
            if alert is not None:
                await self._alerts.raise_(
                    AlertKind.PARSER_DEGRADED,
                    Severity.WARNING,
                    f"Parser degraded ({alert}): messages become raw review cards",
                    dedupe_key=f"parser_degraded:{self._clock.now():%Y%m%d%H}",
                    card_id=card_id,
                    message_id=env.key.message_id,
                    db=db,
                )


class RawReviewPoisonHandler:
    """FR-1e: after `poison_retry_limit` failures: alert + raw review card; the provider's
    next message still processes (the dispatcher already marked this one `failed`)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], alerts: AlertService) -> None:
        self._sessions = sessions
        self._alerts = alerts

    async def on_poison(self, ctx: ProcessingContext, error: str) -> None:
        key = ctx.envelope.key
        async with in_transaction(self._sessions, None) as db:
            card_id = await apply_raw_review(db, ctx, "poison", mark=None)
            await self._alerts.raise_(
                AlertKind.POISON,
                Severity.WARNING,
                f"Message could not be processed after {ctx.attempt} attempts; needs review",
                details={"error": error, "event_type": key.event_type, "event_seq": key.event_seq},
                dedupe_key=f"poison:{key.message_id}:{key.event_type}:{key.event_seq}",
                card_id=card_id,
                message_id=key.message_id,
                db=db,
            )
