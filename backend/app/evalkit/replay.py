"""Replay (PRD §17, TRD §12): the real message graph over labeled, stored messages, with no
side effects. `replay=True` ends the graph after interpretation: no apply, no notify, no cards,
orders, stage timings or system flags, and there is no broker anywhere in `GraphDeps`.

Context is the same C71 context as live, except the provider book: it comes from an in-memory
`Ledger` rebuilt by applying the *labels* of earlier messages in post-time order, so a replay
never depends on today's ledger. Market validation is `skipped` (the graph never reaches it);
field accuracy is still measured. Model calls made by a replay are ordinary `model_calls` rows:
they cannot carry `eval_run_id` because `eval_runs` is insert-only and written after the run.
"""

import time
import uuid
from collections import defaultdict
from dataclasses import replace
from typing import Any

from sqlalchemy import func, select
from sqlmodel import col

from app.dispatcher.types import ProcessingContext
from app.evalkit.labels import Labeled
from app.evalkit.metrics import Case, Sig, compute
from app.evalkit.models import Split
from app.graph.message_graph import GraphDeps, build_message_graph
from app.graph.state import initial_state
from app.inbox.envelope import Envelope, Lane
from app.inbox.models import EventType
from app.interpret.context import BookEntry
from app.interpret.signal import ParsedSignals
from app.llm.models import ModelCall, ModelRole
from app.markettime import to_et
from app.runtime_config.schema import ChainEntry, RuntimeConfig
from app.runtime_config.service import ConfigService

LEDGER_NS = uuid.UUID("f048f048-0000-4000-8000-000000000048")  # position ids of the label ledger
OPENING = frozenset({"entry", "stock_entry"})
CLOSING = frozenset({"full_exit", "stock_exit"})


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
    """The lanes' books (F068) as the labels say they were. ponytail: only opens and full exits
    change it; trims, stops and TPs do not show in the summaries. Upgrade if the book text
    turns out to matter for accuracy."""

    def __init__(self) -> None:
        self._books: defaultdict[Lane, dict[uuid.UUID, tuple[str | None, str]]] = defaultdict(dict)

    def book(self, env: Envelope) -> list[BookEntry]:
        return [BookEntry(id=i, summary=s) for i, (_, s) in self._books[env.lane].items()]

    def apply(self, env: Envelope, label: dict[str, Any]) -> None:
        book = self._books[env.lane]
        link = label.get("link") or {}
        for sig in label["signals"]:
            fields = sig.get("fields", {})
            if sig["msg_type"] in OPENING:
                key = f"{env.key.message_id}.{env.key.event_type}.{env.key.event_seq}.{sig['idx']}"
                book[uuid.uuid5(LEDGER_NS, key)] = (fields.get("underlying"), _summary(env, fields))
            elif sig["msg_type"] in CLOSING:
                linked = link.get("provider_position_id") if link.get("idx") == sig["idx"] else None
                target = uuid.UUID(linked) if linked else None
                if target not in book:  # F047 links name real ledger ids: match by ticker instead
                    target = next(
                        (i for i in reversed(book) if book[i][0] == fields.get("underlying")), None
                    )
                if target is not None:
                    del book[target]


def _summary(env: Envelope, f: dict[str, Any]) -> str:
    underlying = f.get("underlying", "?")
    if "strike" in f:
        right = "C" if f.get("right") == "call" else "P"
        contract = f"{underlying} {f['strike']}{right} exp {f.get('expiry_token', '?')}"
    else:
        contract = f"{underlying} stock"
    at = to_et(env.posted_at).strftime("%Y-%m-%d %H:%M:%S ET")
    entered = f"entered {f['entry_price']} at {at}" if "entry_price" in f else f"opened at {at}"
    return f"{contract}, {entered}"


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
        )
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
        predicted = (
            None
            if state.get("raw_reason") or parsed is None
            else predicted_sigs(ParsedSignals.model_validate(parsed))
        )
        return Case(
            env.provider_id,
            expected_sigs(ev.label),
            predicted,
            state.get("route") or "none",
            latency_ms,
        )


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
