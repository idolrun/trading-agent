"""F029: committed NOTIFYs reach every subscriber; rolled-back ones never; reconnect resumes."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, notify
from app.main import libpq_dsn
from app.notify.bus import Event, EventBus, to_event
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def bus(owner_engine: object) -> AsyncIterator[EventBus]:
    bus = EventBus()
    task = asyncio.create_task(bus.run(libpq_dsn(_url("test_database_url"))))
    yield bus
    task.cancel()


async def take(stream: AsyncIterator[Event], skip: str = "resync") -> Event:
    async with asyncio.timeout(1):  # the SSE budget in the doc
        while (event := await anext(stream)).type == skip:
            pass
    return event


async def send(factory: Factory, channel: str, payload: str, *, commit: bool = True) -> None:
    async with factory() as db:
        await notify(db, channel, payload)
        await (db.commit() if commit else db.rollback())


async def test_committed_notify_reaches_two_subscribers(factory: Factory, bus: EventBus) -> None:
    one, two = bus.stream(), bus.stream()
    first = asyncio.ensure_future(take(one))  # subscribe both before sending
    second = asyncio.ensure_future(take(two))
    await asyncio.sleep(0.3)  # listener connected
    await send(factory, "cards", json.dumps({"type": "card.created", "id": "c1"}), commit=False)
    await send(factory, "cards", json.dumps({"type": "card.created", "id": "c2"}))
    assert (await first).id == (await second).id == "c2"  # the rolled-back c1 never arrives


async def test_reconnect_resumes_delivery(factory: Factory, bus: EventBus) -> None:
    await asyncio.sleep(0.3)  # listener connected
    async with factory() as db:
        killed = await db.scalars(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE query LIKE 'LISTEN%' AND pid <> pg_backend_pid()"
            )
        )
        assert True in killed.all()  # the bus connection really dropped
    await asyncio.sleep(1.5)  # listen_forever reconnects after 1 s
    stream = bus.stream()
    pending = asyncio.ensure_future(take(stream))
    await asyncio.sleep(0.05)  # subscribed
    await send(factory, "alerts", "a-7")  # legacy payload: bare alert id
    assert await pending == Event("alert.created", "a-7")


def test_payload_mapping() -> None:
    assert to_event("flags", "kill_switch") == Event("flag.changed", "kill_switch")
    assert to_event("cards", '{"type": "card.updated", "id": "c9"}') == Event("card.updated", "c9")
    assert to_event("orders", "garbage") is None


async def test_slow_subscriber_is_dropped() -> None:
    bus = EventBus()
    stream = bus.stream()
    first = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)  # the generator is now waiting on its queue: subscribed
    assert bus.subscriber_count == 1
    bus.publish(Event("card.created", "0"))
    assert (await first).id == "0"
    for i in range(1, 1002):  # 1000 fill the unread queue, the next overflows
        bus.publish(Event("card.created", str(i)))
    assert [e async for e in stream] == []  # dropped: the stream ends, the client reconnects
    assert bus.subscriber_count == 0
