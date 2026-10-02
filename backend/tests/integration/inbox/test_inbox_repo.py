import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, in_transaction
from app.inbox import repo
from app.inbox.models import InboxItem, InboxStatus, MessageEvent, SkipReason, StageTiming
from app.ingest.adapter import to_envelope
from app.main import libpq_dsn
from tests.integration.conftest import _url
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B, ac18, create

NOW = datetime(2026, 9, 28, 14, 31, tzinfo=UTC)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def accept(factory: async_sessionmaker[AsyncSession], payload: dict) -> bool:  # type: ignore[type-arg]
    async with in_transaction(factory, None) as db:
        return await repo.accept(db, to_envelope(payload, NOW))


async def count(db: AsyncSession, model: type) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def test_accept_once(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    payload = ac18()["A1"]
    assert await accept(factory, payload) is True
    assert await accept(factory, payload) is False
    assert (await count(db_session, MessageEvent), await count(db_session, InboxItem)) == (1, 1)
    assert await count(db_session, StageTiming) == 1


async def test_concurrent_duplicates_store_one_row(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    payload = ac18()["B2"]
    results = await asyncio.gather(*(accept(factory, payload) for _ in range(5)))
    assert sorted(results) == [False] * 4 + [True]
    assert await count(db_session, MessageEvent) == 1


async def test_notify_only_after_commit(factory: async_sessionmaker[AsyncSession]) -> None:
    async with await psycopg.AsyncConnection.connect(
        libpq_dsn(_url("test_database_url")), autocommit=True
    ) as listener:
        await listener.execute("LISTEN inbox")
        await listener.execute("LISTEN feed")
        notifies = listener.notifies(timeout=0.3)
        async with factory() as db:
            async with db.begin():
                await repo.accept(db, to_envelope(ac18()["A1"], NOW))
                # not committed yet: nothing delivered
                assert [n async for n in listener.notifies(timeout=0.3)] == []
        received = [n async for n in notifies]
    assert [(n.channel, n.payload) for n in received if n.channel == "inbox"] == [
        ("inbox", f"{CHANNEL}:{PROVIDER_A}")
    ]
    feed = [json.loads(n.payload) for n in received if n.channel == "feed"]
    assert [f["type"] for f in feed] == ["feed.message"]


async def test_next_for_lane_in_post_order(factory: async_sessionmaker[AsyncSession]) -> None:
    msgs = ac18()
    for label in ("B2", "A3", "A1", "B4"):  # arrival order differs from post order
        await accept(factory, msgs[label])
    async with in_transaction(factory, None) as db:
        first = await repo.next_for_lane(db, (CHANNEL, PROVIDER_A))
        assert first is not None and first.text == "Bought GOOGL 357.5C @ 1.67"
        attempt = await repo.claim(db, first.key, NOW)
        assert attempt == 1
        # claimed row is no longer "next"
        second = await repo.next_for_lane(db, (CHANNEL, PROVIDER_A))
        assert second is not None and second.text == "Take half off @ 2.10"
        b = await repo.next_for_lane(db, (CHANNEL, PROVIDER_B))
        assert b is not None and b.text == "Bought TSLA 400C @ 2.10"


async def test_mark_claim_reset_and_later_applied(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    msgs = ac18()
    await accept(factory, msgs["A1"])
    await accept(factory, msgs["A3"])
    async with in_transaction(factory, None) as db:
        a1 = await repo.next_for_lane(db, (CHANNEL, PROVIDER_A))
        assert a1 is not None
        await repo.claim(db, a1.key, NOW)
        a3 = await repo.next_for_lane(db, (CHANNEL, PROVIDER_A))
        assert a3 is not None
        await repo.claim(db, a3.key, NOW)
        await repo.mark(db, a3.key, InboxStatus.DONE, skip_reason=SkipReason.MUTED_AUTHOR)
        assert await repo.later_applied(db, (CHANNEL, PROVIDER_A), a1.posted_at)
        assert not await repo.later_applied(db, (CHANNEL, PROVIDER_A), a3.posted_at)
        assert await repo.reset_in_flight(db) == 1  # a1 was left processing
        assert await repo.lanes_waiting(db) == [(CHANNEL, PROVIDER_A)]
    row = await db_session.get(InboxItem, (a3.key.message_id, "create", 0))
    assert row is not None and row.finished_at is not None and row.skip_reason == "muted_author"


async def test_edit_and_create_are_separate_events(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    at = NOW - timedelta(minutes=1)
    payload = create(at, "Bought GOOGL 355C @ 1.67")
    edit = create(at, "Bought GOOGL 357.5C @ 1.67", edited_timestamp=NOW.isoformat())
    edit["type"] = "message.update"
    assert await accept(factory, payload) and await accept(factory, edit)
    assert await count(db_session, MessageEvent) == 2
