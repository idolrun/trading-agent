"""In-process load driver: builds the real pipeline from components (no `create_app`) and feeds
it scheduled messages.

Stack (all real code): `inbox.accept` -> Dispatcher (LISTEN 'inbox', one worker per provider)
-> GraphProcessor (message graph, real Jev client + LLM chain/agent over latency fakes) ->
card row -> approval graph started (checkpointer) -> NOTIFY -> EventBus -> PushService (stamps
`pushed`). Broker: SimulatedBroker; market data: tests/fixtures/market; DB: real PostgreSQL over a
pooled engine.

Latency is measured *in the driver* with `time.perf_counter()`, never from `stage_timings`: those
mix the app clock (`received`/`dequeued`) with DB `now()` (later stages), which disagree when the
app clock is not the wall clock. The app clock here is a `RealtimeClock` that starts at Monday
10:30 ET and advances in real time, so entries are inside regular hours and never stale.

- accept time:  perf_counter before `inbox.accept`'s transaction; its duration is the insert time
- queue wait:   `GraphProcessor.process` start (first attempt) minus the accept commit
- end to end:   `process()` returned (card committed, approval graph started) minus accept start
"""

import asyncio
import itertools
import random
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr
from sqlalchemy import func, make_url, select
from sqlmodel import col

from app.alerts.service import AlertService
from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import ApprovalService
from app.cards.models import Card
from app.config import get_settings
from app.db import create_engine, create_session_factory, in_transaction
from app.dispatcher.service import Dispatcher
from app.dispatcher.types import ProcessingContext, Processor
from app.evalkit.metrics import percentile
from app.execution.consumer import BrokerEventConsumer
from app.execution.exit import ExitPolicy
from app.execution.expiration import ExpirationService
from app.graph.checkpointer import open_pool, saver_and_store
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.inbox.models import InboxItem, Stage, StageTiming
from app.ingest.adapter import to_envelope
from app.interpret.jev.client import JevInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.models import Signal
from app.llm.chain import LlmChain
from app.llm.models import ModelCall
from app.marketdata.fake import FakeMarketData
from app.notify.bus import EventBus
from app.notify.push import PushService
from app.pipeline.processor import RawReviewPoisonHandler
from app.reconcile.service import Reconciler, ReconcileReport
from app.reconcile.startup import recover_after_failover, startup
from app.runtime_config.models import Actor
from app.runtime_config.schema import (
    ChainEntry,
    JevConfig,
    LlmConfig,
    LlmRoles,
    RoleConfig,
    RuntimeConfig,
    TradingMode,
)
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.timers.service import TimerService
from tests.decide_helpers import CONFIG
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import MARKET, T0, Harness
from tests.llm_helpers import FakeRegistry
from tests.load.fakes import LatencyChat, LatencyJev
from tests.load.scenarios import SEED, env_int, latencies
from tests.load.synthetic_day import PROVIDER_BASE, DayMessage
from tests.messages import CHANNEL, create

WARMUP_PROVIDER = PROVIDER_BASE - 1
POOL_SIZE = env_int("LOAD_DB_POOL_SIZE", 50)  # + 10 overflow; production default is 10
LLM = LlmConfig(
    roles=LlmRoles(
        extract=RoleConfig(
            deadline_ms=3000,
            per_attempt_cap_ms=2000,
            chain=[
                ChainEntry(provider="deepseek", model="ds-load", prompt="extract@v1"),
                ChainEntry(provider="openai_compat", model="oss-load", prompt="extract@v1"),
            ],
        )
    )
)


WARMUP = tuple(
    DayMessage(
        0.0,
        WARMUP_PROVIDER,
        f"warm up {path} message",
        "chatter",
        {"msg_type": "chatter"},
        path=path,  # type: ignore[arg-type]
        idx=i,
    )
    for i, path in enumerate(("fast", "slow"))
)


def libpq_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://…` -> `postgresql://…` for raw psycopg connections (LISTEN, graph
    pool). Same as `app.main.libpq_dsn`; not imported so the harness never loads the app."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(False)


def pct(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for no data."""
    return percentile(list(values), q) if values else 0.0


class RealtimeClock:
    """App clock: `start` plus real elapsed time (see module docstring)."""

    def __init__(self, start: datetime) -> None:
        self._start, self._t0 = start, time.perf_counter()

    def now(self) -> datetime:
        return self._start + timedelta(seconds=time.perf_counter() - self._t0)

    def advance(self, delta: timedelta) -> None:
        """Jump ahead (the outage test simulates 10 minutes without waiting them)."""
        self._start += delta


class TimingProcessor:
    """Wraps the real processor; records when each message's first attempt started and when
    its last successful attempt returned, and every exception (a retry or a poison)."""

    def __init__(self, inner: Processor) -> None:
        self.inner = inner
        self.started: dict[int, float] = {}
        self.finished: dict[int, float] = {}
        self.errors: list[tuple[int, str]] = []

    async def process(self, ctx: ProcessingContext) -> None:
        message_id = ctx.envelope.key.message_id
        self.started.setdefault(message_id, time.perf_counter())
        try:
            await self.inner.process(ctx)
        except Exception as exc:
            self.errors.append((message_id, f"{type(exc).__name__}: {exc}"[:200]))
            raise
        self.finished[message_id] = time.perf_counter()


@dataclass
class Sent:
    item: DayMessage
    message_id: int
    env: Envelope
    payload: dict[str, Any] = field(default_factory=dict)  # the Message API payload
    t_accept: float = 0.0  # before inbox.accept
    t_committed: float = 0.0  # accept returned
    accepted: bool = False


@dataclass
class RunResult:
    sent: int
    accepted: int
    wall_s: float
    e2e_s: list[float]
    e2e_by_path: dict[str, list[float]]
    queue_wait_s: dict[int, list[float]]
    insert_ms: list[float]
    statuses: dict[str, int]
    errors: list[tuple[int, str]]
    unfinished: int
    cards: dict[str, int]
    pushed: int
    interpreters: dict[str, int]
    model_outcomes: dict[str, int]
    jev_calls: int
    llm_calls: int
    kinds: dict[str, int] = field(default_factory=dict)
    loop_lag_ms: list[float] = field(default_factory=list)
    slow_by_provider: dict[int, int] = field(default_factory=dict)  # LLM-interpreted messages

    @property
    def failed(self) -> int:
        return self.statuses.get("failed", 0)

    def queue_wait_p95_s(self) -> dict[int, float]:
        return {p: pct(v, 95) for p, v in sorted(self.queue_wait_s.items())}

    def worst_queue_wait_p95_s(self) -> float:
        return max(self.queue_wait_p95_s().values(), default=0.0)


TERMINAL = frozenset({"done", "stale", "failed"})
SEQ = itertools.count(1)  # snowflake low bits: unique across the stacks of one test


class LoadRun:
    """A running stack. `run(items, speed)` replays items at `offset_s / speed` seconds."""

    approvals: ApprovalService
    broker: SimulatedBroker
    dispatcher: Dispatcher
    flags: FlagService
    startup_report: ReconcileReport | None = None  # set when started through `startup()`

    def __init__(
        self,
        h: Harness,
        processor: TimingProcessor,
        jev: LatencyJev,
        chats: list[LatencyChat],
        plans: dict[str, DayMessage],
    ) -> None:
        self.h, self.timing, self.jev, self.chats, self.plans = h, processor, jev, chats, plans
        self.factory = h.factory
        self._lag: list[float] = []

    async def _watch_loop(self) -> None:
        """Event-loop lag: how late a 5 ms sleep wakes (the pipeline shares one loop, as in
        production, so CPU-bound work in any node delays everything else, inserts included)."""
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.005)
            self._lag.append(max(time.perf_counter() - t - 0.005, 0.0) * 1000)

    def envelope(self, item: DayMessage, ids: dict[int, int], at: datetime | None = None) -> Sent:
        """The message as posted at `at` (default: now); `ids` maps item idx -> message id."""
        at = at or self.h.clock.now()
        extra: dict[str, Any] = {}
        if item.reply_to is not None:
            extra["reply_to_id"] = ids[item.reply_to]
        payload = create(at, item.text, author=item.provider, seq=next(SEQ), **extra)
        ids[item.idx] = int(payload["message"]["id"])  # later replies point at it
        return Sent(item, ids[item.idx], to_envelope(payload, at), payload)

    async def accept(self, sent: Sent) -> None:
        sent.t_accept = time.perf_counter()
        async with in_transaction(self.factory, None) as db:
            sent.accepted = await inbox.accept(db, sent.env)
        sent.t_committed = time.perf_counter()

    async def _statuses(self) -> dict[str, int]:
        async with self.factory() as db:
            rows = (
                await db.execute(
                    select(col(InboxItem.status), func.count())
                    .where(col(InboxItem.provider_id) != WARMUP_PROVIDER)
                    .group_by(col(InboxItem.status))
                )
            ).all()
        return {str(s): int(c) for s, c in rows}

    async def drain(self, sent: list[Sent], timeout_s: float) -> None:
        """Until every accepted message's process() returned or it is `failed` (poisoned)."""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:  # the result reports what did not finish
            waiting = [
                s.message_id
                for s in sent
                if s.accepted and s.message_id not in self.timing.finished
            ]
            if not waiting:
                return
            async with self.factory() as db:
                failed = set(
                    (
                        await db.scalars(
                            select(col(InboxItem.message_id)).where(
                                col(InboxItem.message_id).in_(waiting),
                                col(InboxItem.status) == "failed",
                            )
                        )
                    ).all()
                )
            if failed.issuperset(waiting):
                return
            await asyncio.sleep(0.02)

    async def _pushed_settled(self) -> int:
        """`pushed` is stamped by the bus subscriber after the card commit: wait until stable."""
        last, stable = -1, 0
        for _ in range(100):
            async with self.factory() as db:
                count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(StageTiming)
                        .where(col(StageTiming.stage) == Stage.PUSHED)
                    )
                    or 0
                )
            stable = stable + 1 if count == last else 0
            if stable >= 4:
                break
            last = count
            await asyncio.sleep(0.1)
        return last

    async def run(
        self, items: Sequence[DayMessage], *, speed: float = 1.0, drain_timeout_s: float = 120.0
    ) -> RunResult:
        self.timing.errors.clear()
        self._lag = []
        watcher = asyncio.create_task(self._watch_loop(), name="loop-lag")
        self.jev.calls = 0
        for chat in self.chats:
            chat.calls = 0
        ids: dict[int, int] = {}
        sent: list[Sent] = []
        tasks: list[asyncio.Task[None]] = []
        t_start = time.perf_counter()
        for item in items:
            delay = t_start + item.offset_s / speed - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            s = self.envelope(item, ids)
            sent.append(s)
            tasks.append(asyncio.create_task(self.accept(s)))
        await asyncio.gather(*tasks)
        accepted = sum(s.accepted for s in sent)
        await self.drain(sent, drain_timeout_s)
        wall_s = time.perf_counter() - t_start
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await self._pushed_settled()
        return await self._result(sent, accepted, wall_s)

    async def _result(self, sent: list[Sent], accepted: int, wall_s: float) -> RunResult:
        by_id = {s.message_id: s for s in sent}
        async with self.factory() as db:
            interpreter = {
                int(m): str(i)
                for m, i in (
                    await db.execute(select(col(Signal.message_id), col(Signal.interpreter)))
                ).all()
                if int(m) in by_id
            }
            interpreters: dict[str, int] = defaultdict(int)
            for i in interpreter.values():
                interpreters[i] += 1
            card_rows = (
                await db.execute(
                    select(col(Card.action), col(Card.state), func.count()).group_by(
                        col(Card.action), col(Card.state)
                    )
                )
            ).all()
            cards = {f"{a}/{st}": int(c) for a, st, c in card_rows}
            pushed = int(
                await db.scalar(
                    select(func.count())
                    .select_from(StageTiming)
                    .where(col(StageTiming.stage) == Stage.PUSHED)
                )
                or 0
            )
            outcomes = {
                f"{p}:{o}": int(c)
                for p, o, c in (
                    await db.execute(
                        select(col(ModelCall.provider), col(ModelCall.outcome), func.count())
                        .where(col(ModelCall.message_id).in_(list(by_id)))
                        .group_by(col(ModelCall.provider), col(ModelCall.outcome))
                    )
                ).all()
            }
        statuses = await self._statuses()
        e2e: list[float] = []
        by_path: dict[str, list[float]] = defaultdict(list)
        waits: dict[int, list[float]] = defaultdict(list)
        unfinished = 0
        slow_by: dict[int, int] = defaultdict(int)
        for mid, s in by_id.items():
            if not s.accepted:
                continue
            start, end = self.timing.started.get(mid), self.timing.finished.get(mid)
            if start is None or end is None:
                unfinished += 1
                continue
            waits[s.item.provider].append(max(start - s.t_committed, 0.0))
            e2e.append(end - s.t_accept)
            path = {"jev": "fast", "llm": "slow"}.get(interpreter.get(mid, ""), "other")
            slow_by[s.item.provider] += path == "slow"
            by_path[path].append(end - s.t_accept)
        kinds: dict[str, int] = defaultdict(int)
        for s in sent:
            kinds[s.item.kind] += 1
        return RunResult(
            sent=len(sent),
            accepted=accepted,
            wall_s=wall_s,
            e2e_s=e2e,
            e2e_by_path=dict(by_path),
            queue_wait_s=dict(waits),
            insert_ms=[(s.t_committed - s.t_accept) * 1000 for s in sent if s.accepted],
            statuses=statuses,
            errors=[e for e in self.timing.errors if e[0] in by_id],
            unfinished=unfinished,
            cards=cards,
            pushed=pushed,
            interpreters=dict(interpreters),
            model_outcomes=outcomes,
            jev_calls=self.jev.calls,
            llm_calls=sum(c.calls for c in self.chats),
            kinds=dict(kinds),
            loop_lag_ms=list(self._lag),
            slow_by_provider=dict(slow_by),
        )

    async def inbox_statuses(self, message_ids: Sequence[int]) -> dict[int, str]:
        async with self.factory() as db:
            rows = await db.execute(
                select(col(InboxItem.message_id), col(InboxItem.status)).where(
                    col(InboxItem.message_id).in_(list(message_ids))
                )
            )
        return {int(m): str(s) for m, s in rows.all()}

    async def wait_terminal(self, message_ids: Sequence[int], timeout_s: float) -> dict[int, str]:
        """Until every event is done / stale / failed (or the timeout); the last statuses."""
        deadline = time.perf_counter() + timeout_s
        while True:
            statuses = await self.inbox_statuses(message_ids)
            done = all(statuses.get(m) in TERMINAL for m in message_ids)
            if done or time.perf_counter() > deadline:
                return statuses
            await asyncio.sleep(0.05)

    async def consume(self) -> None:
        """Apply every pending broker event (fills -> lots), like the running consumer."""
        consumer = BrokerEventConsumer(self.factory, [self.broker])
        while events := self.broker.pending_events():
            for event in events:
                await consumer.handle(event)

    async def warm_up(self) -> None:
        """First-use costs (agent graph compile, connection setup, prompt loading) belong to
        process start, not to a message: push one fast and one forced-slow chatter through."""
        for item in WARMUP:
            res = await self.run([item])
            if res.failed or res.errors or res.unfinished:
                raise RuntimeError(f"warm-up failed: {res.errors} {res.statuses}")


@asynccontextmanager
async def open_run(
    fixtures_dir: Path,
    plans: dict[str, DayMessage],
    *,
    force_slow: bool = False,
    seed: int = SEED,
    warm_up: bool = True,
    clock: RealtimeClock | None = None,
    broker: SimulatedBroker | None = None,
    restart: bool = False,
    trading_mode: TradingMode = TradingMode.SHADOW,
) -> AsyncIterator[LoadRun]:
    """Build and start the stack against the test database; tear it down on exit.

    Fault tests pass what outlives a process: the `clock` (time goes on) and the `broker` (an
    external system). `restart`: start through the real TRD §9
    `startup()` sequence (block entries, reconcile, expire / resume approvals,
    timers, dispatcher, unblock) instead of `Dispatcher.start()` alone."""
    plans = {**plans, **{m.text: m for m in WARMUP}}  # the fakes copy their plans
    url = _url("test_database_url")
    settings = get_settings().model_copy(
        update={
            "database_url": url,
            "db_pool_size": POOL_SIZE,
            "deepseek_api_key": SecretStr("k"),
            "openai_compat_api_key": SecretStr("k"),
        }
    )
    engine = create_engine(settings)  # pooled, like the app
    factory = create_session_factory(engine)
    pool = open_pool(libpq_dsn(url), max_size=10)
    await pool.open()
    tasks: list[asyncio.Task[Any]] = []
    dispatcher: Dispatcher | None = None
    exits: ExitPolicy | None = None
    try:
        saver, _ = saver_and_store(pool)
        clock = clock or RealtimeClock(T0 + timedelta(seconds=5))
        config = ConfigService(factory, allow_ungated_models=True)
        await config.load()  # a row left by an interrupted run must not be a version conflict
        snapshot = config.snapshot()
        await config.update(
            CONFIG.model_copy(
                update={
                    "channels": [CHANNEL],
                    "llm": LLM,
                    "jev": JevConfig(model="jev-load", enabled=True),
                    "trading_mode": trading_mode,
                }
            ),
            expected_version=snapshot[0] if snapshot else 0,
            actor=Actor.CLI,
            reason="load test",
        )
        # like `Harness.create`, but the harness only calls clock.now() and a FixedClock would
        # freeze time for the whole run
        h = Harness(
            factory,
            FakeMarketData.from_fixtures(MARKET, clock),
            config,
            AlertService(factory),
            fixtures_dir,
            clock,  # type: ignore[arg-type]
        )
        jev_latency, llm_latency = latencies()
        rng = random.Random(seed)  # noqa: S311 — seeded simulation, not crypto
        fake_jev = LatencyJev(plans, jev_latency, rng, force_slow=force_slow)
        chats = [
            LatencyChat(responses=[], plans=plans, latency=llm_latency, rng=rng) for _ in range(2)
        ]
        models: dict[str, BaseChatModel] = {"deepseek": chats[0], "openai_compat": chats[1]}
        chain = LlmChain(
            factory,
            settings,
            lambda: (h.config.get() or RuntimeConfig()).llm,
            clock,
            models=FakeRegistry(models),
        )
        jev = JevInterpreter(
            factory,
            settings,
            lambda: h.config.get() or RuntimeConfig(),
            clock,
            client=fake_jev,  # type: ignore[arg-type]
        )
        broker = broker or SimulatedBroker(h.md, h.clock)
        approvals = h.approval_service(saver, broker)
        exits = approvals._executor.exit
        processor = h.processor(
            fixture=False,
            llm=LlmInterpreter(chain, h.md, h.alerts),
            jev=jev,
            checkpointer=saver,
            approvals=approvals,
        )
        timing = TimingProcessor(processor)

        # bus + push: `pushed` is stamped when a card.created reaches the subscriber
        bus = EventBus()
        push = PushService(factory, bus, settings, sender=lambda _info, _data: None)
        ready = asyncio.Event()

        async def probe() -> None:
            async for event in bus.stream():
                if event.type == "resync":
                    ready.set()
                    return

        tasks.append(asyncio.create_task(probe(), name="bus-probe"))
        tasks.append(asyncio.create_task(push.run(), name="push"))
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(bus.run(libpq_dsn(url)), name="bus"))
        async with asyncio.timeout(15):
            await ready.wait()

        flags = FlagService(factory, h.alerts)
        reconciler = Reconciler(
            factory,
            broker=broker,
            consumer=BrokerEventConsumer(factory, [broker]),
            flags=flags,
            alerts=h.alerts,
            config=h.config,
            clock=clock,
        )
        timers = TimerService(factory, alerts=h.alerts, clock=clock)
        dispatcher = Dispatcher(
            factory,
            config=h.config,
            processor=timing,
            poison=RawReviewPoisonHandler(factory, h.alerts),
            clock=clock,
            listen_dsn=libpq_dsn(url),
            idle_exit_s=3600,
            on_recover=partial(  # as `create_app` wires it
                recover_after_failover,
                flags=flags,
                reconciler=reconciler,
                approvals=approvals,
                timers=timers,
            ),
        )
        run = LoadRun(h, timing, fake_jev, chats, plans)
        run.approvals, run.broker, run.dispatcher = approvals, broker, dispatcher
        run.flags = flags
        if restart:
            assert exits is not None  # the harness always wires exits
            run.startup_report = await startup(
                sessions=factory,
                flags=flags,
                reconciler=reconciler,
                approvals=approvals,
                timers=timers,
                expiration=ExpirationService(
                    factory,
                    config=h.config,
                    clock=clock,
                    approvals=approvals,
                    exits=exits,
                    alerts=h.alerts,
                ),
                exits=exits,
                dispatcher=dispatcher,
            )
        else:
            await dispatcher.start()
        await _warm_pool(factory)
        if warm_up:
            await run.warm_up()
        yield run
    finally:
        if dispatcher is not None:
            await dispatcher.stop()
        if exits is not None:  # no exit re-price loop outlives its stack
            tasks += exits.loops.values()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await pool.close()
        await engine.dispose()


async def _warm_pool(factory: Any) -> None:
    """Open `POOL_SIZE` connections up front, as a long-running process would have."""
    sessions = [factory() for _ in range(POOL_SIZE)]
    try:
        await asyncio.gather(*(s.execute(select(1)) for s in sessions))
    finally:
        await asyncio.gather(*(s.close() for s in sessions))
