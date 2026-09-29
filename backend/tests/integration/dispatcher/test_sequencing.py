"""F012: per-provider ordering, parallel providers, poison, skips, restart, heartbeat."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.db import in_transaction
from app.dispatcher.service import AlertPoisonHandler, Dispatcher
from app.dispatcher.stub import StubProcessor
from app.dispatcher.types import ProcessingContext
from app.inbox import repo as inbox
from app.inbox.models import InboxItem
from app.ingest.adapter import to_envelope
from app.main import libpq_dsn
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.heartbeat import last_beat, run_heartbeat
from tests.integration.conftest import _url
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B, ac18, create

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


async def make_dispatcher(
    factory: Factory, stub: StubProcessor, *, listen: bool = False, **config: Any
) -> Dispatcher:
    service = ConfigService(factory)
    await service.update(
        RuntimeConfig(channels=[CHANNEL], **config),
        expected_version=0,
        actor=Actor.CLI,
        reason="test",
    )
    return Dispatcher(
        factory,
        config=service,
        processor=stub,
        poison=AlertPoisonHandler(AlertService(factory)),
        clock=FixedClock(T0 + timedelta(seconds=40)),
        listen_dsn=libpq_dsn(_url("test_database_url")) if listen else None,
        idle_exit_s=0.3,
    )


async def accept(factory: Factory, *payloads: dict[str, Any]) -> None:
    for payload in payloads:
        async with in_transaction(factory, None) as db:
            await inbox.accept(db, to_envelope(payload, T0))


async def statuses(db: AsyncSession) -> dict[str, int]:
    rows = (await db.scalars(select(InboxItem.status))).all()
    return {s: rows.count(s) for s in set(rows)}


async def eventually(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while not await check():  # noqa: ASYNC110 — polling DB state written by other tasks
            await asyncio.sleep(0.05)


async def all_finished(factory: Factory, n: int) -> None:
    async def check() -> bool:
        async with factory() as db:
            s = await statuses(db)
        return sum(v for k, v in s.items() if k in ("done", "stale", "failed")) == n

    await eventually(check)


async def test_ac18_order_per_provider_and_parallel_providers(factory: Factory) -> None:
    msgs = ac18()
    await accept(factory, msgs["B2"], msgs["A3"], msgs["A1"], msgs["B4"])  # arrival order
    stub = StubProcessor(factory, delay_s=0.2)
    dispatcher = await make_dispatcher(factory, stub)
    await dispatcher.start()
    await all_finished(factory, 4)
    await dispatcher.stop()

    texts = {v["message"]["id"]: k for k, v in msgs.items()}
    by_provider = {
        p: [texts[str(c.key.message_id)] for c in stub.calls if c.provider_id == p]
        for p in (PROVIDER_A, PROVIDER_B)
    }
    assert by_provider == {PROVIDER_A: ["A1", "A3"], PROVIDER_B: ["B2", "B4"]}
    a1 = next(c for c in stub.calls if c.provider_id == PROVIDER_A)
    b2 = next(c for c in stub.calls if c.provider_id == PROVIDER_B)
    assert a1.started < b2.ended and b2.started < a1.ended  # A and B overlap
    a_calls = [c for c in stub.calls if c.provider_id == PROVIDER_A]
    assert a_calls[0].ended <= a_calls[1].started  # A strictly serial


async def test_live_notify_wakes_workers(factory: Factory, db_session: AsyncSession) -> None:
    stub = StubProcessor(factory)
    dispatcher = await make_dispatcher(factory, stub, listen=True)
    await dispatcher.start()
    await asyncio.sleep(0.2)  # listener connected
    await accept(factory, ac18()["A1"])
    await all_finished(factory, 1)
    await asyncio.sleep(0.5)  # worker idles out ...
    await accept(factory, ac18()["A3"])  # ... and is respawned by the next NOTIFY
    await all_finished(factory, 2)
    await dispatcher.stop()
    assert len(stub.calls) == 2


async def test_poison_does_not_block_the_provider(
    factory: Factory, db_session: AsyncSession
) -> None:
    a1, a2, a3 = (create(T0 + timedelta(seconds=s), f"msg {s}") for s in (0, 5, 10))
    poison_id = int(a2["message"]["id"])

    def fail(ctx: ProcessingContext) -> bool:
        return ctx.envelope.key.message_id == poison_id

    stub = StubProcessor(factory, fail=fail)
    await accept(factory, a1, a2, a3)
    dispatcher = await make_dispatcher(factory, stub)
    await dispatcher.start()
    await all_finished(factory, 3)
    await dispatcher.stop()

    row = await db_session.get(InboxItem, (poison_id, "create", 0))
    assert row is not None
    assert (row.status, row.attempts) == ("failed", 3)
    assert row.last_error == "RuntimeError: stub processor: scripted failure"
    assert [c.key.message_id for c in stub.calls] == [
        int(a1["message"]["id"]),
        int(a3["message"]["id"]),
    ]
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and (alert.kind, alert.message_id) == ("poison", poison_id)


async def test_muted_and_unconfigured_are_visible_skips(
    factory: Factory, db_session: AsyncSession
) -> None:
    muted = create(T0, "from B", author=PROVIDER_B)
    elsewhere = create(T0 + timedelta(seconds=1), "other channel", channel=123)
    stub = StubProcessor(factory)
    await accept(factory, muted, elsewhere)
    dispatcher = await make_dispatcher(factory, stub, muted_authors=[PROVIDER_B])
    await dispatcher.start()
    await all_finished(factory, 2)
    await dispatcher.stop()
    assert stub.calls == []
    rows = (await db_session.scalars(select(InboxItem))).all()
    assert sorted((r.status, r.skip_reason) for r in rows) == [
        ("done", "channel_not_configured"),
        ("done", "muted_author"),
    ]


async def test_restart_requeues_in_flight(factory: Factory) -> None:
    await accept(factory, ac18()["A1"])
    async with in_transaction(factory, None) as db:
        env = await inbox.next_for_provider(db, PROVIDER_A)
        assert env is not None
        await inbox.claim(db, env.key, T0)  # crashed mid-processing
    stub = StubProcessor(factory)
    dispatcher = await make_dispatcher(factory, stub)
    await dispatcher.start()
    await all_finished(factory, 1)
    await dispatcher.stop()
    assert len(stub.calls) == 1


async def test_stale_and_late_flags(factory: Factory, db_session: AsyncSession) -> None:
    msgs = ac18()
    stub = StubProcessor(factory)
    await accept(factory, msgs["A3"])
    dispatcher = await make_dispatcher(factory, stub)
    await dispatcher.start()
    await all_finished(factory, 1)
    await accept(factory, msgs["A1"])  # arrives after A3 was applied
    dispatcher.wake(PROVIDER_A)
    await all_finished(factory, 2)
    await dispatcher.stop()
    a1 = await db_session.get(InboxItem, (int(msgs["A1"]["message"]["id"]), "create", 0))
    assert a1 is not None and a1.late is True
    assert a1.status == "stale"  # 40 s old > stale_entry_s 30


async def test_heartbeat_cadence(factory: Factory) -> None:
    clock = FixedClock(T0)
    task = asyncio.create_task(run_heartbeat(factory, clock, interval_s=0.05))
    beats = []
    for _ in range(3):
        clock.advance(timedelta(seconds=5))
        await asyncio.sleep(0.08)
        async with factory() as db:
            beats.append(await last_beat(db))
    task.cancel()
    assert beats[0] is not None and beats[0] < beats[-1]  # type: ignore[operator]
