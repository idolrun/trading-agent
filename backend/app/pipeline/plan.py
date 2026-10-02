"""Planning for one message event, before any lock (F026, used by the message graph F035):
validate -> link -> scale follow-up prices -> provider-ledger effect -> decide, per signal in
order over an in-memory copy of the provider's book. All I/O is here; `apply_event` writes.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.broker.port import BrokerClient
from app.cards.models import CardAction, CardType
from app.decide.engine import decide
from app.decide.risk_snapshot import build_risk_snapshot
from app.decide.types import DecisionInputs, FlagsView, LotView, ProposedAction, RiskSnapshot
from app.dispatcher.types import ProcessingContext
from app.interpret.models import LinkMethod, LinkOutcome, MsgType
from app.interpret.signal import ParsedSignal, ParsedSignals
from app.ledger import repo as ledger
from app.ledger.correlation import LinkDecision, LinkInputs, resolve_link
from app.ledger.positions import (
    OPENING,
    InvalidTransition,
    PositionState,
    PositionUpdate,
    apply_to_position,
    contract_of,
)
from app.ledger.resolver import Resolution
from app.logging import bind
from app.marketdata.freshness import is_stale
from app.marketdata.port import MarketData
from app.pipeline.apply import SignalOutcome, position_id_for, signal_id_for
from app.runtime_config.schema import RuntimeConfig
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


class Planner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        validation: ValidationService,
        marketdata: MarketData,
        flags: FlagService,
        broker: Callable[[], BrokerClient | None] | None = None,
    ) -> None:
        self._broker = broker  # paper / live broker for the account (None in shadow mode)
        self._sessions = sessions
        self._validation = validation
        self._md = marketdata
        self._flags = flags

    async def plan(
        self,
        ctx: ProcessingContext,
        parsed: ParsedSignals,
        config: RuntimeConfig,
        book: Book,
        resolution: Resolution | None,
        now: datetime,
    ) -> list[SignalOutcome]:
        """`book`: the lane's live positions; `resolution`: the context resolver's verdict for
        this message (F069); None = no evidence, every book position a candidate."""
        env = ctx.envelope
        method = {
            "jev": LinkMethod.JEV,
            "laya": LinkMethod.LAYA,
            "fast": LinkMethod.CODE,
        }.get(parsed.interpreter, LinkMethod.LLM)
        flags = await self._flags_view()
        risk = None
        if any(s.msg_type in SIZED_TYPES for s in parsed.signals):
            risk = await self._risk(now, config)

        outcomes = []
        for idx, sig in enumerate(parsed.signals):  # in order: later signals see earlier effects
            signal_id = signal_id_for(env.key, idx)
            with bind(signal_id=str(signal_id)):
                v = await self._validation.validate(sig, env, config=config, now=now)
                norm = v.normalized
                link = None
                if norm is not None:
                    link = resolve_link(
                        norm,
                        sig.link_hint,
                        LinkInputs(env.provider_id, dict(book), resolution),
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
                    v = await self._scale_follow_up(v, norm, position, config, now)
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

    async def redecide(
        self,
        ctx: ProcessingContext,
        sig: ParsedSignal,
        link: LinkDecision | None,
        position_id: uuid.UUID | None,
        position: PositionState | None,
        config: RuntimeConfig,
        *,
        posted_at: datetime,
        now: datetime,
    ) -> list[ProposedAction]:
        """Decide an already-applied signal again against today's ledger (off-hours release,
        F039): fresh validation, quotes, flags and risk; no ledger effect. `posted_at` is the
        validity origin (OD-29: the release time)."""
        v = await self._validation.validate(sig, ctx.envelope, config=config, now=now)
        norm = v.normalized
        if position is not None and position.instrument == "option" and norm is not None:
            v = await self._scale_follow_up(v, norm, position, config, now)
            norm = v.normalized
        return decide(
            DecisionInputs(
                signal=sig,
                validation=v,
                link=link,
                position=position,
                lot=await self._lot(position_id) if position is not None else None,
                config=config,
                tick=await self._tick(sig, v, position, norm),
                posted_at=posted_at,
                flags=await self._flags_view(),
                risk=await self._risk(now, config) if sig.msg_type in SIZED_TYPES else None,
            )
        )

    async def confirm_underlying(
        self,
        ctx: ProcessingContext,
        sig: ParsedSignal,
        link: LinkDecision | None,
        config: RuntimeConfig,
        *,
        now: datetime,
    ) -> tuple[list[ProposedAction], PositionUpdate | None]:
        """AC-14 / FR-14: an opening whose underlying the user just confirmed (`sig` carries
        it). Validated and decided again, plus the provider-ledger opening the pipeline skipped
        while the underlying was unknown (validation was not ok, so no position was written)."""
        v = await self._validation.validate(sig, ctx.envelope, config=config, now=now)
        update = None
        if v.status == "ok" and v.normalized is not None:
            try:
                update = apply_to_position(None, v.normalized)
            except InvalidTransition:
                logger.warning("confirmed opening has no ledger effect", exc_info=True)
        proposals = decide(
            DecisionInputs(
                signal=sig,
                validation=v,
                link=link,
                position=None,
                lot=None,
                config=config,
                tick=await self._tick(sig, v, None, v.normalized),
                posted_at=ctx.envelope.posted_at,
                flags=await self._flags_view(),
                risk=await self._risk(now, config),
            )
        )
        return proposals, update

    async def _flags_view(self) -> FlagsView:
        return FlagsView(
            kill_switch=await self._flags.is_active(FlagName.KILL_SWITCH),
            entries_blocked=await self._flags.is_active(FlagName.ENTRIES_BLOCKED),
        )

    async def _scale_follow_up(
        self,
        v: ValidationResult,
        norm: NormalizedSignal,
        position: PositionState,
        config: RuntimeConfig,
        now: datetime,
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
        if is_stale(quote, now, config.quote_max_age_s):
            return v.model_copy(update={"status": "review", "reasons": ["quote_stale"]})
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

    async def _risk(self, now: datetime, config: RuntimeConfig) -> RiskSnapshot | None:
        try:
            return await build_risk_snapshot(
                self._sessions,
                self._md,
                now,
                broker=self._broker() if self._broker else None,
                max_quote_age_s=config.quote_max_age_s,
            )
        except Exception:
            logger.exception("risk snapshot failed")
            return None  # entries disabled "risk unknown"
