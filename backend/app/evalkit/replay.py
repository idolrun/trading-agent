"""Replay (PRD §17, TRD §12): the real message graph over labeled, stored messages, with no
side effects. `replay=True` ends the graph after interpretation: no apply, no notify, no cards,
orders, stage timings or system flags, and there is no broker anywhere in `GraphDeps`.

Context is the same C71 context as live, except where the lane's history comes from: an
in-memory `Ledger` rebuilt by applying the *labels* of earlier messages in post-time order
(positions, their states and which message opened or acted on which position), so a replay never
depends on today's ledger. From it the replay builds the provider book with the production
`summarize`, and the resolution with the production `resolve_context` (reply chain, thread,
stated contract, sole candidate, re-entry priors; F082); follow-up links are scored with the
production `resolve_link`. The one difference from live: history from labels, not from DB rows.
Market validation is `skipped` (the graph never reaches it);
field accuracy is still measured. Model calls made by a replay are ordinary `model_calls` rows:
they cannot carry `eval_run_id` because `eval_runs` is insert-only and written after the run.
"""

import time
import uuid
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlmodel import col

from app.dispatcher.types import ProcessingContext
from app.evalkit.labels import Labeled
from app.evalkit.metrics import Case, Sig, compute
from app.evalkit.models import Split
from app.graph.message_graph import GraphDeps, build_message_graph, interpret_outcome
from app.graph.state import initial_state
from app.inbox.envelope import Envelope, Lane
from app.inbox.models import EventType
from app.interpret.candidates import find_candidates
from app.interpret.context import BookEntry, summarize
from app.interpret.models import LinkMethod, LinkOutcome
from app.interpret.signal import ParsedSignals
from app.ledger.correlation import FOLLOW_UPS, LinkInputs, resolve_link
from app.ledger.models import Instrument, PositionStatus, ProviderPosition
from app.ledger.positions import LIVE, SETUPS, state_of
from app.ledger.resolver import (
    REENTRY,
    REPLY_DEPTH,
    Evidence,
    ParentLink,
    Prior,
    Resolution,
    resolve_context,
)
from app.llm.models import ModelCall, ModelRole
from app.markettime import et_day_start, resolve_expiry_token
from app.runtime_config.schema import ChainEntry, RuntimeConfig
from app.runtime_config.service import ConfigService
from app.validate.normalize import NeedsReview, identify

LEDGER_NS = uuid.UUID("f048f048-0000-4000-8000-000000000048")  # position ids of the label ledger
OPENING = frozenset({"entry", "stock_entry"})
CLOSING = frozenset({"full_exit", "stock_exit"})
SETUP = frozenset({"watch", "conditional"})


class _Pinned(ConfigService):
    """The config of one run (a pinned model, or one sweep point); nothing is loaded or saved."""

    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg

    def get(self) -> RuntimeConfig:
        return self._cfg


def parse_model_ref(model_ref: str) -> ChainEntry:
    """`provider:model:prompt` (the model name may itself contain colons)."""
    provider, _, rest = model_ref.partition(":")
    model, _, prompt = rest.rpartition(":")
    return ChainEntry(provider=provider, model=model, prompt=prompt)


def pin_extract(cfg: RuntimeConfig, entry: ChainEntry) -> RuntimeConfig:
    """`cfg` with the slow-path chain reduced to `entry`: the run judges that model alone."""
    roles = cfg.llm.roles
    chain = roles.extract.model_copy(update={"chain": [entry]})
    llm = cfg.llm.model_copy(update={"roles": roles.model_copy(update={"extract": chain})})
    return cfg.model_copy(update={"llm": llm})


class Ledger:
    """The lanes' history (F068) as the labels say it was (F082): every labeled position as an
    in-memory `ProviderPosition` (never added to a session), its status after each labeled
    follow-up, and which message opened or acted on which position (the replay's
    `message_links`), so the production summary and resolver run on it unchanged."""

    def __init__(self) -> None:
        self._positions: defaultdict[Lane, dict[uuid.UUID, ProviderPosition]] = defaultdict(dict)
        self._opened_by: dict[uuid.UUID, int] = {}  # position -> the message that opened it
        self._links: defaultdict[int, list[ParentLink]] = defaultdict(list)  # message -> links
        self._replies: dict[int, int | None] = {}  # message -> the message it replies to
        self._threads: defaultdict[tuple[int, int], set[uuid.UUID]] = defaultdict(set)

    def _lane_book(self, env: Envelope) -> list[ProviderPosition]:
        """`context_repo.lane_book_filter`: live positions + the setups opened today."""
        day = et_day_start(env.posted_at)
        return [
            p
            for p in self._positions[env.lane].values()
            if p.status in LIVE or (p.status in SETUPS and p.opened_at >= day)
        ]

    def book(self, env: Envelope) -> list[BookEntry]:
        return [
            BookEntry(id=p.provider_position_id, summary=summarize(p)) for p in self._lane_book(env)
        ]

    def states(self, env: Envelope) -> dict[uuid.UUID, Any]:
        return {p.provider_position_id: state_of(p) for p in self._lane_book(env)}

    def resolution(self, env: Envelope) -> Resolution:
        """`ledger.repo.load_resolution` over the label history (same pure resolver)."""
        hops: list[tuple[ParentLink, ...]] = []
        message_id = env.reply_to_id
        for _ in range(REPLY_DEPTH):
            if message_id is None:
                break
            hops.append(tuple(self._links.get(message_id, ())))
            message_id = self._replies.get(message_id)
        thread = (
            frozenset(self._threads.get((env.thread_id, env.provider_id), ()))
            if env.thread_id is not None
            else frozenset()
        )
        text = env.text or ""
        live = {k: v for k, v in self.states(env).items() if v.status in LIVE}
        resolution = resolve_context(
            provider_id=env.provider_id,
            book=live,
            reply_hops=hops,
            thread_positions=thread,
            evidence=Evidence.of(text, find_candidates(text)),
        )
        if not REENTRY.search(text):
            return resolution
        day = et_day_start(env.posted_at)
        priors = tuple(
            Prior(
                position_id=p.provider_position_id,
                status=p.status,
                instrument=p.instrument,
                underlying=p.underlying,
                expiry=p.expiry,
                strike=p.strike,
                right=p.option_right,
                opened_message_id=self._opened_by.get(p.provider_position_id),
            )
            for p in reversed(self._positions[env.lane].values())
            if (p.status == PositionStatus.CLOSED and p.closed_at and p.closed_at >= day)
            or (p.status in SETUPS and p.opened_at >= day)
        )
        return resolution.model_copy(update={"reentry": priors})

    def apply(self, env: Envelope, label: dict[str, Any]) -> None:
        self._replies[env.key.message_id] = env.reply_to_id
        lane = self._positions[env.lane]
        for sig in label["signals"]:
            kind, fields = sig["msg_type"], sig.get("fields", {})
            target = self.target(env, label, sig)
            # an add with nothing to add to: the position opened before the corpus; the label
            # says it exists, so later follow-ups can link to it (user review 2026-10-08)
            opens = kind in OPENING or kind == "add" or (kind in SETUP and fields.get("underlying"))
            if opens and target is None:
                position = _position(env, sig)
                lane[position.provider_position_id] = position
                self._opened_by[position.provider_position_id] = env.key.message_id
                target = position.provider_position_id
            elif target is not None and kind in CLOSING:
                lane[target].status, lane[target].closed_at = PositionStatus.CLOSED, env.posted_at
            elif target is not None and kind == "trim":
                lane[target].status = PositionStatus.TRIMMED
                lane[target].remaining_fraction = Decimal("0.5")  # ponytail: the summary only
            if target is not None:
                self._links[env.key.message_id].append(ParentLink(env.provider_id, target))
                if env.thread_id is not None:
                    self._threads[(env.thread_id, env.provider_id)].add(target)

    def target(self, env: Envelope, label: dict[str, Any], sig: dict[str, Any]) -> uuid.UUID | None:
        """The labeled position of a signal, as an id of this lane's label ledger."""
        lane = self._positions[env.lane]
        link = label.get("link") or {}
        linked = link.get("provider_position_id") if link.get("idx") == sig["idx"] else None
        target = uuid.UUID(linked) if linked else None
        if target is None and sig["msg_type"] in FOLLOW_UPS:  # F047 links name real ledger ids
            underlying = sig.get("fields", {}).get("underlying")
            target = next(
                (i for i in reversed(lane) if underlying and lane[i].underlying == underlying),
                None,
            )
        return target if target in lane else None


def position_id_of(message_key: str, idx: int = 0) -> uuid.UUID:
    """The label-ledger id of the position opened by signal `idx` of `message_key`
    (`<message_id>.<event_type>.<event_seq>`): what a label's `link` names."""
    return uuid.uuid5(LEDGER_NS, f"{message_key}.{idx}")


def _position(env: Envelope, sig: dict[str, Any]) -> ProviderPosition:
    f = sig.get("fields", {})
    stock = sig["msg_type"] == "stock_entry" or sig.get("instrument") == "stock"
    setup = sig["msg_type"] in SETUP
    key = f"{env.key.message_id}.{env.key.event_type}.{env.key.event_seq}"
    token = f.get("expiry_token")
    return ProviderPosition(
        provider_position_id=position_id_of(key, sig["idx"]),
        provider_id=env.provider_id,
        opened_channel_id=env.channel_id,
        instrument=None
        if setup and "strike" not in f
        else (Instrument.STOCK if stock else Instrument.OPTION),
        underlying=str(f.get("underlying", "?")).upper(),
        expiry=resolve_expiry_token(token, env.posted_at) if token else None,
        strike=_decimal(f.get("strike")),
        option_right=f.get("right"),
        provider_entry_price=_decimal(f.get("entry_price")),
        stop_level=_decimal(f.get("stop_level")),
        status=PositionStatus(sig["msg_type"]) if setup else PositionStatus.OPEN,
        condition_text=(env.text or None) if sig["msg_type"] == "conditional" else None,
        opened_at=env.posted_at,
    )


def _decimal(v: Any) -> Decimal | None:
    try:
        return None if v is None else Decimal(str(v))
    except ArithmeticError:
        return None


def expected_sigs(label: dict[str, Any]) -> dict[int, Sig]:
    return {
        s["idx"]: Sig(
            s["msg_type"],
            {
                **s.get("fields", {}),
                **({"instrument": s["instrument"]} if s.get("instrument") else {}),
            },
        )
        for s in label["signals"]
    }


def _plain(v: Any) -> Any:
    if isinstance(v, dict) and "value" in v and "source" in v:
        return v["value"]
    return [_plain(i) for i in v] if isinstance(v, list) else v


def predicted_sigs(parsed: ParsedSignals) -> dict[int, Sig]:
    """Answers as plain values; `normal` = no field came from context (C31)."""
    out: dict[int, Sig] = {}
    for idx, s in enumerate(parsed.signals):
        raw = s.fields.model_dump(mode="json")
        cells = [c for v in raw.values() for c in (v if isinstance(v, list) else [v])]
        normal = all(
            c["source"]["kind"] != "context" for c in cells if isinstance(c, dict) and "source" in c
        )
        fields = {k: _plain(v) for k, v in raw.items()}
        if s.instrument:
            fields["instrument"] = s.instrument
        out[idx] = Sig(s.msg_type, fields, normal)
    return out


class Replayer:
    def __init__(self, deps: GraphDeps, cfg: RuntimeConfig | None = None) -> None:
        # Nothing that can act on an order or a card may be reachable from a replay.
        actors = (deps.approvals, deps.relink, deps.notes, deps.corrections)
        assert all(a is None for a in actors), "replay never reaches a broker"  # noqa: S101
        self._ledger = Ledger()
        pinned = replace(
            deps,
            config=deps.config if cfg is None else _Pinned(cfg),
            book=self._ledger.book,
            resolution=self._ledger.resolution,
        )
        self._cfg = pinned.cfg
        self._graph = build_message_graph(pinned)

    async def run(self, events: list[Labeled], split: Split) -> list[Case]:
        """`events`: every label in post-time order (the ledger needs all of them); only
        `split`'s events are interpreted and scored. Deletes have nothing to interpret."""
        cases: list[Case] = []
        for ev in events:
            env = ev.envelope
            if ev.split == split and env.key.event_type != EventType.DELETE:
                cases.append(await self._case(ev))
            self._ledger.apply(env, ev.label)
        return cases

    async def _case(self, ev: Labeled) -> Case:
        env = ev.envelope
        ctx = ProcessingContext(
            envelope=env, attempt=1, started_at=env.posted_at, age_s=0, stale=False, late=False
        )
        t0 = time.perf_counter()
        state = await self._graph.ainvoke(initial_state(ctx.model_dump(mode="json"), replay=True))
        latency_ms = (time.perf_counter() - t0) * 1000
        parsed = state.get("parsed")
        answered = None if state.get("raw_reason") or parsed is None else parsed
        signals = ParsedSignals.model_validate(answered) if answered is not None else None
        outcome, _ = interpret_outcome(state)  # type: ignore[arg-type]
        resolution = Resolution.model_validate(state["resolution"])
        return Case(
            env.provider_id,
            expected_sigs(ev.label),
            predicted_sigs(signals) if signals is not None else None,
            state.get("route") or "none",
            latency_ms,
            link=self._link(ev, signals, resolution),
            outcome=str(outcome),
        )

    def _link(
        self, ev: Labeled, parsed: ParsedSignals | None, resolution: Resolution
    ) -> str | None:
        """F082: score a labeled follow-up's link the way live code decides it: production
        `identify` + `resolve_link` over the label ledger's lane book and the run's resolution.
        `ambiguous`: no link made (a review card, or the follow-up was not recognized)."""
        env, label = ev.envelope, ev.label
        link = label.get("link") or {}
        sig = next((s for s in label["signals"] if s["idx"] == link.get("idx")), None)
        if sig is None or sig["msg_type"] not in FOLLOW_UPS:
            return None
        expected = self._ledger.target(env, label, sig)
        if expected is None:
            return None
        got = parsed.signals[sig["idx"]] if parsed and sig["idx"] < len(parsed.signals) else None
        if got is None:
            return "ambiguous"
        cfg = self._cfg()
        norm = identify(got, now=env.posted_at, config=cfg)
        if isinstance(norm, NeedsReview):
            return "ambiguous"
        states = self._ledger.states(env)
        decision = resolve_link(
            norm,
            got.link_hint,
            LinkInputs(
                env.provider_id,
                {k: v for k, v in states.items() if v.status in LIVE},
                resolution,
                {k: v for k, v in states.items() if v.status in SETUPS},
            ),
            t_link=cfg.t_link,
            hint_method=LinkMethod.JEV,
        )
        if decision is None or decision.outcome != LinkOutcome.LINKED:
            return "ambiguous"
        return "correct" if decision.position_id == expected else "wrong"


async def measure(
    deps: GraphDeps, events: list[Labeled], split: Split, cfg: RuntimeConfig | None = None
) -> dict[str, Any]:
    """Replay `split` and return its metrics, cost included (model calls made since the start)."""
    started = deps.clock.now()
    cases = await Replayer(deps, cfg).run(events, split)
    ids = {e.envelope.key.message_id for e in events if e.split == split}
    async with deps.sessions() as db:
        cost = await db.scalar(
            select(func.coalesce(func.sum(col(ModelCall.cost_usd)), 0)).where(
                col(ModelCall.message_id).in_(ids),
                col(ModelCall.started_at) >= started,
                col(ModelCall.role).in_([ModelRole.INTERPRET, ModelRole.EXTRACT]),
            )
        )
    return compute(cases, cost_usd=float(cost or 0))
