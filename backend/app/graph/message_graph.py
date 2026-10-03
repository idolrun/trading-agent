"""The message graph (TRD §2.1, F035): one run per message event, under the lane slot (F068).

    load_context (lane-scoped C71 context)
    -> resolve_context (F069: code-only reply chain / thread / stated contract -> candidates)
    -> find_candidates
    -> fixture_interpret (dev/tests) -> decide_apply
    -> fast_interpret (F070: whole-message rules, no model) -> decide_apply
       else jev_interpret -> route: feed_only | assemble -> decide_apply
          slow_path, by why (F078):
            model_failure (off, timeout, API error, invalid answer) -> laya_interpret
            semantic (unsure, several signals, missing field, condition) -> llm_extract
            link (which earlier position: re-entry among several) -> raw_review
          scope violation (user data in the request) -> raw_review + urgent alert, no model
    laya_interpret (only after a Jev model failure): feed_only | assemble -> decide_apply,
                   link doubt -> raw_review, else -> llm_extract (optional)
    llm_extract (not configured / exhausted / rejected) | decide_apply (span mismatch) -> raw_review
    decide_apply | raw_review -> notify -> END
    recheck mode (F037): after interpretation -> END (no apply, no notify)
    replay mode (F048): as recheck, and additionally no stage_timings, no system flags, no user
    lots, the provider book from `GraphDeps.book` (evalkit's label ledger) and `now` = post time

Every run ends with one `interpret_outcome` (`interpret_outcome()`), stored on the inbox row
with the per-interpreter trace (F078).
Models only choose among the resolver's candidates; `decide_apply` links with the resolver's
verdict and cross-checks any model pick against the message's stated contract (F069).
Nodes are thin adapters over the existing services. Side effects: only `decide_apply` /
`raw_review` (one DB transaction each) and `notify` (post-commit). Models are side-effect free
apart from their `model_calls` audit rows. The graph ends at notify, which starts the per-card
approval graphs (F036).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.cards.approval_service import ApprovalService
from app.cards.corrections import CorrectionService
from app.cards.models import Card, CardAction, CardState
from app.db import in_transaction
from app.dispatcher.types import ProcessingContext
from app.graph.state import MessageState
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.inbox.models import EventType, InboxStatus, Stage
from app.interpret import context_repo
from app.interpret.candidates import Candidates, find_candidates
from app.interpret.context import BookEntry, ProviderContext, load_context, summarize
from app.interpret.fast import fast_parse
from app.interpret.feed_guard import feed_block
from app.interpret.interpreter import Interpreter, InterpreterUnavailable
from app.interpret.jev.client import JevAnswered, JevFailed, JevInterpreter
from app.interpret.jev.mapping import FEED_TYPES
from app.interpret.laya.client import LayaInterpreter, gate
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.llm_extract.middleware import ScopeViolation
from app.interpret.models import Interpreter as InterpreterKind
from app.interpret.note import NoteWriter
from app.interpret.route import InterpretOutcome, route
from app.interpret.signal import ParsedSignals
from app.interpret.spans import verify_literal_spans
from app.ledger import repo as ledger
from app.ledger.positions import state_of
from app.ledger.relink import RelinkService
from app.ledger.resolver import Resolution
from app.llm.usage import CallRef
from app.markettime import Clock, is_regular_hours, next_regular_open
from app.observability.counters import bump
from app.pipeline.apply import ApplyInput, apply_event, apply_raw_review, record_stage
from app.pipeline.plan import Planner
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName

logger = logging.getLogger(__name__)
PROFILE_KEYS = ("lexicon", "habits", "aliases")  # store namespace ("provider", author_id)


@dataclass
class GraphDeps:
    sessions: async_sessionmaker[AsyncSession]
    config: ConfigService
    clock: Clock
    planner: Planner
    flags: FlagService
    llm: LlmInterpreter
    jev: JevInterpreter | None = None
    laya: LayaInterpreter | None = None  # only when Jev gave no answer
    notes: NoteWriter | None = None
    store: BaseStore | None = None
    fixture: Interpreter | None = None  # dev/demo/tests only: replaces Jev + LLM
    approvals: ApprovalService | None = None  # None: cards wait (tests of the graph alone)
    relink: RelinkService | None = None  # F037: re-check later follow-ups after a late event
    corrections: CorrectionService | None = None  # F038; None: edits/deletes are feed-only
    book: Callable[[Envelope], list[BookEntry]] | None = None  # replay only: the label ledger
    alerts: AlertService | None = None  # F078: a scope violation is an urgent alert
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def cfg(self) -> RuntimeConfig:
        return self.config.get() or RuntimeConfig()


def build_message_graph(
    deps: GraphDeps, checkpointer: Any = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    n = _Nodes(deps)
    g = StateGraph(MessageState)
    for name, fn in (
        ("load_context", n.load_context),
        ("resolve_context", n.resolve_context),
        ("find_candidates", n.find_candidates),
        ("fixture_interpret", n.fixture_interpret),
        ("fast_interpret", n.fast_interpret),
        ("jev_interpret", n.jev_interpret),
        ("laya_interpret", n.laya_interpret),
        ("llm_extract", n.llm_extract),
        ("decide_apply", n.decide_apply),
        ("raw_review", n.raw_review),
        ("notify", n.notify),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", lambda s: "notify" if s["skip"] else "resolve_context")
    g.add_edge("resolve_context", "find_candidates")
    g.add_conditional_edges(
        "find_candidates",
        lambda _s: "fixture_interpret" if deps.fixture is not None else "fast_interpret",
    )
    g.add_conditional_edges("fixture_interpret", _after_interpret)
    # F070: an obvious message is settled by code; Jev is never called for it
    g.add_conditional_edges(
        "fast_interpret",
        lambda s: _after_interpret(s) if s["route"] == "fast" else "jev_interpret",
    )
    # F078: what Jev cannot settle goes where its reason says (see module docstring)
    g.add_conditional_edges("jev_interpret", _after_jev)
    g.add_conditional_edges(
        "laya_interpret",
        lambda s: (
            "llm_extract"
            if s["route"] == "slow_path" and not s["raw_reason"]
            else _after_interpret(s)
        ),
    )
    g.add_conditional_edges("llm_extract", _after_interpret)
    g.add_conditional_edges("decide_apply", _raw_review_or("notify"))  # span mismatch
    g.add_edge("raw_review", "notify")
    g.add_edge("notify", END)
    return g.compile(checkpointer=checkpointer)


def _raw_review_or(next_node: str) -> Any:
    return lambda s: "raw_review" if s["raw_reason"] else next_node


def _after_interpret(s: MessageState) -> str:
    if s["recheck"] or s["replay"]:
        return END
    return "raw_review" if s["raw_reason"] else "decide_apply"


def _after_jev(s: MessageState) -> str:
    if s["raw_reason"] or s["route"] != "slow_path":
        return _after_interpret(s)
    return "laya_interpret" if s["fallback"] == "model_failure" else "llm_extract"


def interpret_outcome(s: MessageState) -> tuple[InterpretOutcome, dict[str, Any]]:
    """F078: the run's one outcome and its trace (inbox.interpret_outcome / _reasons)."""
    trace = {**s.get("trace", {}), "route": s.get("route") or None}
    if s.get("route_reasons"):
        trace["reasons"] = s["route_reasons"]
    raw = s.get("raw_reason")
    if raw:
        trace["raw_reason"] = raw
        if raw == "scope_violation":
            return InterpretOutcome.SCOPE_VIOLATION, trace
        if raw == "literal_span_mismatch":
            return InterpretOutcome.VALIDATION_FAILURE, trace
        return InterpretOutcome.REVIEW, trace
    match s.get("interpreter"):
        case "fast":
            return InterpretOutcome.FAST_SUCCESS, trace
        case "jev":
            return InterpretOutcome.JEV_SUCCESS, trace
        case "laya" | "llm":
            return InterpretOutcome.FALLBACK_SUCCESS, trace
        case _:
            return InterpretOutcome.FIXTURE, trace


class _Nodes:
    def __init__(self, deps: GraphDeps) -> None:
        self.d = deps

    async def load_context(self, state: MessageState) -> dict[str, Any]:
        ctx = _processing(state)
        env = ctx.envelope
        replay = state["replay"]
        if env.key.event_type == EventType.DELETE or (
            env.key.event_type == EventType.EDIT and self.d.corrections is None and not replay
        ):
            invalidated: list[uuid.UUID] = []
            async with in_transaction(self.d.sessions, None) as db:
                if env.key.event_type == EventType.DELETE and self.d.corrections is not None:
                    invalidated = await self.d.corrections.handle_delete(db, env)  # 7.5
                await inbox.mark(db, env.key, InboxStatus.DONE, late=ctx.late)
            return {"skip": True, "superseded_ids": [str(c) for c in invalidated]}
        cfg = self.d.cfg()
        profile = await self._profile(env.provider_id)
        labelled = self.d.book(env) if replay and self.d.book else None
        async with in_transaction(self.d.sessions, None) as db:
            # the lane book is read once: for the model's context and for the resolver (F069)
            rows = (
                []
                if labelled is not None
                else await context_repo.open_book(db, env.lane, env.posted_at)
            )
            context = await load_context(
                db,
                env,
                recent_n=cfg.context_recent_messages,
                now=env.posted_at if replay else self.d.clock.now(),
                profile=profile,
                book=labelled
                if labelled is not None
                else [BookEntry(id=r.provider_position_id, summary=summarize(r)) for r in rows],
            )
            if labelled is not None:  # the label ledger has summaries only: all candidates
                resolution = Resolution.unresolved({b.id: b for b in labelled})
            else:
                states = {r.provider_position_id: state_of(r) for r in rows}
                resolution = await ledger.load_resolution(db, env, states)
            lots = [] if replay else await ledger.open_lots(db)  # the user's lots: never replayed
            if not replay:
                await record_stage(db, env.key, ctx.attempt, Stage.CONTEXT)
        return {
            "context": context.model_dump(mode="json"),
            "resolution": resolution.model_dump(mode="json"),
            "lot_ids": [str(lot.lot_id) for lot in lots],
        }

    async def resolve_context(self, state: MessageState) -> dict[str, Any]:
        """F069: which lane positions the message can be about, from code-only evidence (reply
        chain, thread, stated contract; read in `load_context`'s transaction). Narrows what a
        model may link to."""
        context = _context(state)
        resolution = Resolution.model_validate(state["resolution"])
        allowed = set(resolution.candidates)
        narrowed = context.model_copy(
            update={
                "link_candidates": [b for b in context.provider_book if b.id in allowed],
                "reentry_candidates": [
                    BookEntry(id=p.position_id, summary=p.summary) for p in resolution.reentry
                ],
            }
        )
        return {
            "resolution": resolution.model_dump(mode="json"),
            "context": narrowed.model_dump(mode="json"),
        }

    async def find_candidates(self, state: MessageState) -> dict[str, Any]:
        text = _processing(state).envelope.text or ""
        return {"candidates": find_candidates(text).model_dump(mode="json")}

    async def fixture_interpret(self, state: MessageState) -> dict[str, Any]:
        assert self.d.fixture is not None  # noqa: S101 — edge only taken when injected
        try:
            parsed = await self.d.fixture.interpret(_context(state), _candidates(state))
        except InterpreterUnavailable:
            return {"raw_reason": "parser_degraded"}
        kind = "none" if parsed.interpreter == "stub" else parsed.interpreter
        return {"route": "fixture", "parsed": parsed.model_dump(mode="json"), "interpreter": kind}

    async def fast_interpret(self, state: MessageState) -> dict[str, Any]:
        """F070: whole-message rules, literal spans only; follow-ups only on a unique
        resolution. Anything else continues to Jev."""
        ctx = _processing(state)
        env = ctx.envelope
        if env.key.event_type != EventType.CREATE:
            return {}
        resolution = (
            Resolution.model_validate(state["resolution"]) if state.get("resolution") else None
        )
        parsed = fast_parse(env.text or "", resolution, sorted(self.d.cfg().option_tiers or {}))
        if parsed is None:
            return {}
        await self._stage(state, ctx, Stage.FAST)
        return {
            "route": "fast",
            "parsed": parsed.model_dump(mode="json"),
            "interpreter": "fast",
        }

    async def jev_interpret(self, state: MessageState) -> dict[str, Any]:
        ctx = _processing(state)
        outcome: JevAnswered | JevFailed = JevFailed("disabled")
        if self.d.jev is not None:
            outcome = await self.d.jev.interpret(
                _model_context(state),
                _candidates(state),
                ref=_ref(ctx),
                lot_ids=frozenset(state["lot_ids"]),
                # No LLM to infer it from context: an option entry that names no ticker still
                # becomes a card with its contract filled in and the user confirms the
                # underlying (FR-14), instead of a blank review card.
                underlying_optional=not self.d.llm.configured(),
                resolution=state.get("resolution"),
            )
            if not (isinstance(outcome, JevFailed) and outcome.kind == "disabled"):
                await self._stage(state, ctx, Stage.JEV)
        if isinstance(outcome, JevFailed):
            if outcome.kind == "scope_violation":
                await self._scope_alert(ctx, "Jev", outcome.detail, outcome.model_call_id)
                return {
                    "raw_reason": "scope_violation",
                    "trace": {"jev": "scope_violation"},
                    "model_call_id": outcome.model_call_id,
                }
            status = "invalid" if outcome.kind == "invalid" else "model_failure"
            return {
                "route": "slow_path",
                "fallback": "model_failure",
                "route_reasons": [f"jev_{outcome.kind}"],
                "trace": {"jev": status, "jev_detail": outcome.kind},
                "interpreter": "jev",
                "model_call_id": outcome.model_call_id,
                "jev_answered": False,
            }
        result = outcome.result
        decision = route(result, self.d.cfg(), self._feed_block(state))
        update: dict[str, Any] = {
            "route": decision.path,
            "route_reasons": list(decision.reasons),
            "fallback": decision.kind,
            "hints": decision.hints,
            "parsed": decision.parsed.model_dump(mode="json") if decision.parsed else None,
            "interpreter": "jev",
            "model_call_id": result.model_call_id,
            "jev_answered": True,
            "trace": {"jev": "ambiguous" if decision.path == "slow_path" else "success"},
        }
        if decision.path == "slow_path":
            bump("jev:ambiguous")
            if decision.kind == "link":  # which earlier position: a person decides (F077)
                update["raw_reason"] = f"needs_review: {'; '.join(decision.reasons)}"[:200]
        return update

    async def laya_interpret(self, state: MessageState) -> dict[str, Any]:
        """Jev gave no usable answer (F078: off, timeout, API error, invalid answer): the local
        model gets one try, under its own strict thresholds. What it cannot settle continues to
        the optional LLM, then to review; a doubt about which position is meant is a review."""
        ctx = _processing(state)
        result = None
        asked = self.d.laya is not None and self.d.laya.available()
        trace = dict(state["trace"])
        if self.d.laya is not None:
            try:
                result = await self.d.laya.interpret(
                    _model_context(state),
                    _candidates(state),
                    ref=_ref(ctx),
                    lot_ids=frozenset(state["lot_ids"]),
                    underlying_optional=not self.d.llm.configured(),
                )
            except ScopeViolation as exc:
                await self._scope_alert(ctx, "Laya", str(exc), None)
                return {"raw_reason": "scope_violation", "trace": trace | {"laya": "scope"}}
        if asked:  # F074: timed whether it answered, timed out or was busy
            await self._stage(state, ctx, Stage.LAYA)
        if result is None:
            return {"trace": trace | {"laya": "no_answer" if asked else "off"}}
        decision = route(result, gate(self.d.cfg()), self._feed_block(state))
        settled = decision.path != "slow_path"
        reasons = (
            list(decision.reasons)
            if settled
            else [*state["route_reasons"], *(f"laya: {r}" for r in decision.reasons)]
        )
        update: dict[str, Any] = {
            "route": decision.path,
            "route_reasons": reasons,
            "fallback": decision.kind,
            "parsed": decision.parsed.model_dump(mode="json") if decision.parsed else None,
            "interpreter": "laya",
            "model_call_id": result.model_call_id,
            "trace": trace | {"laya": "success" if settled else "unsure"},
        }
        if decision.kind == "link":
            update["raw_reason"] = f"needs_review: {'; '.join(reasons)}"[:200]
        return update

    async def llm_extract(self, state: MessageState) -> dict[str, Any]:
        if not self.d.llm.configured():
            # The LLM is optional (4-8 s per call): without one the human decides, and the
            # card says why the local models did not. No degraded-parser flag: nothing failed.
            reasons = "; ".join(state["route_reasons"])
            return {
                "raw_reason": f"needs_review: {reasons}"[:200],
                "trace": state["trace"] | {"llm": "not_configured"},
            }
        ctx = _processing(state)
        deadline = self.d.cfg().llm.roles.extract.deadline_ms
        try:
            async with asyncio.timeout(None if deadline is None else deadline / 1000):
                result = await self.d.llm.extract(
                    _model_context(state),
                    _candidates(state),
                    ref=_ref(ctx),
                    hints=state["hints"],
                    lot_ids=frozenset(state["lot_ids"]),
                )
        except TimeoutError:
            result = None
        await self._stage(state, ctx, Stage.LLM)
        if result is None or result.status == "exhausted":  # FR-9
            if not state["replay"]:
                await self.d.flags.set(
                    FlagName.PARSER_DEGRADED,
                    True,
                    "LLM chain exhausted or timed out: messages become raw review cards",
                    Actor.SYSTEM,
                )
            return {"raw_reason": "parser_degraded", "trace": state["trace"] | {"llm": "exhausted"}}
        if result.status == "content_rejected":  # no answer shopping (TRD §4.8 rule 3)
            return {
                "raw_reason": f"llm_content_rejected:{result.reason or ''}"[:200],
                "trace": state["trace"] | {"llm": "rejected"},
            }
        if not state["replay"]:
            await self.d.flags.set(
                FlagName.PARSER_DEGRADED, False, "LLM answered again", Actor.SYSTEM
            )
        assert result.value is not None  # noqa: S101 — status ok
        feed = all(sig.msg_type in FEED_TYPES for sig in result.value.signals)
        if feed and (block := self._feed_block(state)) is not None:  # F079: the last model too
            return {
                "raw_reason": f"needs_review: feed_guard: {block} with a position open"[:200],
                "trace": state["trace"] | {"llm": "feed_guard"},
                "model_call_id": result.last.model_call_id if result.last else None,
            }
        return {
            "parsed": result.value.model_dump(mode="json"),
            "trace": state["trace"] | {"llm": "success"},
            "interpreter": "llm",
            "model_call_id": result.last.model_call_id if result.last else None,
        }

    async def decide_apply(self, state: MessageState) -> dict[str, Any]:
        """validate -> link -> decide (pure, pre-tx) then the one apply transaction. Kept in
        one node so decided outcomes never pass through the checkpoint."""
        ctx = _processing(state)
        env = ctx.envelope
        parsed = (
            ParsedSignals.model_validate(state["parsed"])
            if state["parsed"] is not None
            else ParsedSignals(signals=[], interpreter="none")
        )
        if verify_literal_spans(parsed, env.text or ""):  # AC-11, every interpreter
            return {"raw_reason": "literal_span_mismatch"}
        cfg = self.d.cfg()
        async with self.d.sessions() as db:
            rows = await context_repo.open_book(db, env.lane, env.posted_at)
        book = {r.provider_position_id: state_of(r) for r in rows}
        resolution = (
            Resolution.model_validate(state["resolution"]) if state.get("resolution") else None
        )
        version = {r.provider_position_id: r.updated_at for r in rows}
        planner = (
            self.d.corrections
            if self.d.corrections is not None and self.d.corrections.applies(env, parsed)
            else self.d.planner
        )  # F038: edits and `correction` signals correct the original's position
        now = self.d.clock.now()
        outcome, trace = interpret_outcome(state)
        outcomes = await planner.plan(ctx, parsed, cfg, book, resolution, now)
        await self._stage(state, ctx, Stage.VALIDATED)
        async with in_transaction(self.d.sessions, None) as db:
            result = await apply_event(
                db,
                ApplyInput(
                    ctx,
                    InterpreterKind(state["interpreter"] or "none"),
                    version,
                    outcomes,
                    model_call_id=state["model_call_id"],
                    hold_until=None if is_regular_hours(now) else next_regular_open(now),
                    outcome=outcome,
                    trace=trace,
                ),
            )
        return {
            "card_ids": [str(c) for c in result.card_ids],
            "superseded_ids": [str(c) for c in result.superseded_card_ids],
        }

    async def raw_review(self, state: MessageState) -> dict[str, Any]:
        ctx = _processing(state)
        outcome, trace = interpret_outcome(state)
        async with in_transaction(self.d.sessions, None) as db:
            card_id = await apply_raw_review(
                db,
                ctx,
                state["raw_reason"] or "raw_review",
                mark=InboxStatus.DONE,
                interpreted=outcome,
                trace=trace,
            )
        return {"card_ids": [str(card_id)] if card_id else []}

    async def notify(self, state: MessageState) -> dict[str, Any]:
        """Post-commit. SSE/push already flow from the apply transaction's NOTIFY (F029).
        Here: resume the approval graphs of superseded cards, start one for every new pending
        card (F036), and the advisory note off the critical path (F034)."""
        ids = [uuid.UUID(c) for c in state["card_ids"]]
        pending: list[tuple[uuid.UUID, CardAction]] = []
        if ids:
            async with self.d.sessions() as db:
                rows = await db.execute(
                    select(col(Card.card_id), col(Card.action)).where(
                        col(Card.card_id).in_(ids), col(Card.state) == CardState.PENDING
                    )
                )
                pending = [(r[0], r[1]) for r in rows.all()]
        ctx = _processing(state)
        relinks: list[uuid.UUID] = []
        if ctx.late and not state["skip"]:
            logger.info("late event applied", extra={"provider_id": ctx.envelope.provider_id})
            if self.d.relink is not None:  # FR-1b: never a silent feed-only
                relinks = await self.d.relink.recheck_after_late(
                    ctx.envelope.lane, ctx.envelope.posted_at
                )
        if self.d.approvals is not None:
            by = ctx.envelope.key.message_id
            await self.d.approvals.invalidated([uuid.UUID(c) for c in state["superseded_ids"]], by)
            await self.d.approvals.start_many([*(card_id for card_id, _ in pending), *relinks])
        if self.d.notes is not None:
            for card_id, action in pending:
                if action == CardAction.REVIEW:
                    continue
                task = asyncio.create_task(self._note(card_id), name=f"note:{card_id}")
                self.d.background.add(task)
                task.add_done_callback(self.d.background.discard)
        return {}

    async def _note(self, card_id: uuid.UUID) -> None:
        assert self.d.notes is not None  # noqa: S101
        try:
            await self.d.notes.write(card_id)
        except Exception:
            logger.exception("advisory note failed", extra={"card_id": str(card_id)})

    async def _profile(self, author_id: int) -> dict[str, list[str]]:
        """Long-term provider profile (TRD §5.3). Written only by manual ops (F047)."""
        if self.d.store is None:
            return {}
        profile: dict[str, list[str]] = {}
        for key in PROFILE_KEYS:
            item = await self.d.store.aget(("provider", str(author_id)), key)
            if item is not None and isinstance(item.value.get("items"), list):
                profile[key] = [str(v) for v in item.value["items"]]
        return profile

    def _feed_block(self, state: MessageState) -> str | None:
        """F079: the whole lane book (live positions and today's setups), not the narrowed one."""
        text = _processing(state).envelope.text or ""
        return feed_block(text, _context(state), _candidates(state))

    async def _scope_alert(
        self, ctx: ProcessingContext, model: str, reason: str, call_id: int | None
    ) -> None:
        """F078: user-portfolio data reached a model request: nothing was sent; urgent."""
        if self.d.alerts is None:
            return
        await self.d.alerts.raise_(
            AlertKind.INVARIANT_BLOCK,
            Severity.URGENT,
            f"{model} request blocked: it contained user-portfolio data (FR-8)",
            details={"reason": reason, "model_call_id": call_id},
            message_id=ctx.envelope.key.message_id,
        )

    async def _stage(self, state: MessageState, ctx: ProcessingContext, stage: Stage) -> None:
        if state["replay"]:
            return
        async with in_transaction(self.d.sessions, None) as db:
            await record_stage(db, ctx.envelope.key, ctx.attempt, stage)


def _processing(state: MessageState) -> ProcessingContext:
    return ProcessingContext.model_validate(state["processing"])


def _context(state: MessageState) -> ProviderContext:
    return ProviderContext.model_validate(state["context"])


def _model_context(state: MessageState) -> ProviderContext:
    """What a model sees (F078): the resolver-narrowed positions as the book, never the whole
    lane book (fewer tokens, nothing to confuse the pick with)."""
    ctx = _context(state)
    return ctx.model_copy(update={"provider_book": ctx.link_candidates})


def _candidates(state: MessageState) -> Candidates:
    return Candidates.model_validate(state["candidates"])


def _ref(ctx: ProcessingContext) -> CallRef:
    key = ctx.envelope.key
    return CallRef(message_id=key.message_id, event_type=key.event_type, event_seq=key.event_seq)


def thread_id(ctx: ProcessingContext) -> str:
    """Attempt-scoped: a crash re-run starts fresh from load_context (TRD §9)."""
    key = ctx.envelope.key
    return f"msg:{key.message_id}:{key.event_type}:{key.event_seq}:{ctx.attempt}"
