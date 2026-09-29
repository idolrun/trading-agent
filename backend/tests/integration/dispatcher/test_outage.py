"""Database outage while the dispatcher runs (F063 test 6 findings): a failed event is released
once the database answers, the worker survives, connection errors never poison, and workers
hold until the failover recovery (block entries, reconcile) has run, retried until it does.
Only database driver errors are an outage; an applied event is never rewound."""

import asyncio
from typing import Any

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.alerts.models import Alert
from app.broker.errors import BrokerUnavailable
from app.dispatcher.stub import StubProcessor
from app.dispatcher.types import ProcessingContext
from app.inbox import repo as inbox
from app.inbox.models import InboxItem, InboxStatus
from tests.integration.dispatcher.test_sequencing import (
    Factory,
    accept,
    all_finished,
    eventually,
    make_dispatcher,
    statuses,
)
from tests.messages import ac18


class DownOnce(StubProcessor):
    """The database drops while A1 is processed (`attempts` times): a connection error."""

    def __init__(self, factory: Factory, message_id: int, attempts: int) -> None:
        super().__init__(factory)
        self._target, self._left = message_id, attempts

    async def process(self, ctx: ProcessingContext) -> None:
        if ctx.envelope.key.message_id == self._target and self._left > 0:
            self._left -= 1
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        await super().process(ctx)


def refuse_marks(monkeypatch: pytest.MonkeyPatch, times: int) -> list[InboxStatus]:
    """`inbox.mark` of a failed event raises while the database is down."""
    real, seen = inbox.mark, []

    async def mark(db: Any, key: Any, status: InboxStatus, **kw: Any) -> bool:
        if status == InboxStatus.RECEIVED and len(seen) < times:
            seen.append(status)
            raise psycopg.OperationalError("connection refused")
        return await real(db, key, status, **kw)

    monkeypatch.setattr(inbox, "mark", mark)
    return seen


async def test_outage_while_releasing_a_failed_event_keeps_order_and_never_poisons(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"], msgs["A3"])
    a1 = int(msgs["A1"]["message"]["id"])
    stub = DownOnce(factory, a1, attempts=3)  # 3 >= poison_retry_limit: an outage is not poison
    refused = refuse_marks(monkeypatch, times=2)
    recovered: list[int] = []

    async def on_recover() -> None:
        recovered.append(1)

    dispatcher = await make_dispatcher(factory, stub)
    dispatcher._on_recover = on_recover
    await dispatcher.start()
    try:
        await all_finished(factory, 2)
    finally:
        await dispatcher.stop()

    assert len(refused) == 2 and recovered  # the mark was retried, recovery ran
    async with factory() as db:
        final = await statuses(db)
    assert set(final) <= {"done", "stale"} and sum(final.values()) == 2  # none stuck or failed
    texts = {int(v["message"]["id"]): k for k, v in msgs.items()}
    assert [texts[c.key.message_id] for c in stub.calls] == ["A1", "A3"]  # order kept


async def test_workers_hold_until_failover_recovery_ran(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"])
    stub = StubProcessor(factory)
    real, failures = inbox.claim, [OperationalError("UPDATE inbox", None, ConnectionRefusedError())]

    async def claim(*args: Any, **kw: Any) -> int:
        if failures:
            raise failures.pop()
        return await real(*args, **kw)

    monkeypatch.setattr(inbox, "claim", claim)
    events: list[str] = []
    gate = asyncio.Event()

    async def on_recover() -> None:
        events.append("recover")
        await gate.wait()  # e.g. reconciliation still running

    dispatcher = await make_dispatcher(factory, stub)
    dispatcher._on_recover = on_recover
    await dispatcher.start()
    try:

        async def recovering() -> bool:
            return events == ["recover"]

        await eventually(recovering)
        await asyncio.sleep(0.2)
        assert stub.calls == []  # held while recovery runs
        gate.set()
        await all_finished(factory, 1)
    finally:
        await dispatcher.stop()
    assert len(stub.calls) == 1


async def test_listener_reconnect_triggers_recovery_once(factory: Factory) -> None:
    calls: list[int] = []

    async def on_recover() -> None:
        calls.append(1)

    dispatcher = await make_dispatcher(factory, StubProcessor(factory))
    dispatcher._on_recover = on_recover
    await dispatcher._on_connect()  # first connect: discover only
    assert calls == []
    await dispatcher._on_connect()  # reconnect after the database went away
    await dispatcher._on_connect()  # while recovering: not a second run
    assert dispatcher._recovery is not None
    await dispatcher._recovery
    assert calls == [1]
    await dispatcher.stop()


async def test_recovery_retries_a_broker_error_and_workers_stay_held(factory: Factory) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"])
    stub = StubProcessor(factory)
    calls: list[int] = []

    async def on_recover() -> None:  # entries are blocked by now
        calls.append(1)
        if len(calls) == 1:
            raise BrokerUnavailable("broker down while reconciling")

    dispatcher = await make_dispatcher(factory, stub)
    dispatcher._on_recover = on_recover
    dispatcher.recover()
    await dispatcher.start()
    try:

        async def failed_once() -> bool:
            return calls == [1]

        await eventually(failed_once)
        await asyncio.sleep(0.1)  # inside the retry backoff
        assert stub.calls == [] and not dispatcher._ready.is_set()  # still held
        await all_finished(factory, 1)
    finally:
        await dispatcher.stop()
    assert calls == [1, 1] and len(stub.calls) == 1  # retried, then processing resumed


class TimesOut(StubProcessor):
    async def process(self, ctx: ProcessingContext) -> None:
        raise TimeoutError("broker call timed out")


async def test_timeout_is_the_messages_failure_poisons_and_starts_no_recovery(
    factory: Factory,
) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"])
    recovered: list[int] = []

    async def on_recover() -> None:
        recovered.append(1)

    dispatcher = await make_dispatcher(factory, TimesOut(factory))
    dispatcher._on_recover = on_recover
    await dispatcher.start()
    try:
        await all_finished(factory, 1)
    finally:
        await dispatcher.stop()

    async with factory() as db:
        row = await db.get(InboxItem, (int(msgs["A1"]["message"]["id"]), "create", 0))
        alert = await db.scalar(select(Alert))
    assert row is not None and (row.status, row.attempts) == ("failed", 3)
    assert row.last_error == "TimeoutError: broker call timed out"
    assert alert is not None and alert.kind == "poison"
    assert recovered == [] and dispatcher._recovery is None


class Blocking(StubProcessor):
    """Holds its `process()` open until released: an event in flight when the database drops."""

    def __init__(self, factory: Factory, events: list[str]) -> None:
        super().__init__(factory)
        self.events, self.started, self.release = events, asyncio.Event(), asyncio.Event()

    async def process(self, ctx: ProcessingContext) -> None:
        self.started.set()
        await self.release.wait()
        self.events.append("processed")
        await super().process(ctx)


async def test_failover_recovery_waits_for_in_flight_processing(factory: Factory) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"], msgs["A3"])
    events: list[str] = []
    stub = Blocking(factory, events)

    async def on_recover() -> None:
        events.append("recover")

    dispatcher = await make_dispatcher(factory, stub)
    dispatcher._on_recover = on_recover
    await dispatcher.start()
    try:
        async with asyncio.timeout(5):
            await stub.started.wait()
        dispatcher.recover()  # the database went away while A1 is in process()
        await asyncio.sleep(0.2)
        assert events == []  # recovery waits for the provider slot
        stub.release.set()
        assert dispatcher._recovery is not None
        await dispatcher._recovery
        await all_finished(factory, 2)
    finally:
        await dispatcher.stop()
    assert events == ["processed", "recover", "processed"]  # A3 only after recovery


class AppliedThenFails(StubProcessor):
    """Apply committed (`done`), then a post-apply step (e.g. notify) raises."""

    async def process(self, ctx: ProcessingContext) -> None:
        await super().process(ctx)
        raise RuntimeError("notify failed")


async def test_failure_after_apply_does_not_rewind_an_applied_event(factory: Factory) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"])
    dispatcher = await make_dispatcher(factory, AppliedThenFails(factory), poison_retry_limit=1)
    await dispatcher.start()
    try:
        await all_finished(factory, 1)

        async def idle() -> bool:
            return not dispatcher.busy()

        await eventually(idle)
    finally:
        await dispatcher.stop()

    async with factory() as db:
        row = await db.get(InboxItem, (int(msgs["A1"]["message"]["id"]), "create", 0))
        alert = await db.scalar(select(Alert))
    assert row is not None and row.status in ("done", "stale")  # still applied, not rewound
    assert (row.attempts, row.last_error) == (1, None)
    assert alert is None  # attempt 1 >= limit 1, but an applied event is not poison
