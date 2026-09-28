"""F003: ingestion & interpretation tables (database-schema.md §3)."""

import uuid

from psycopg import errors
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.models import MessageEvent
from app.interpret.models import MessageLink, Signal
from app.llm.models import ModelCall
from tests.integration.schema.helpers import T0, add_inbox, add_message, expect_error

EVENT_SQL = """
    INSERT INTO message_events
        (message_id, event_type, event_seq, provider_id, channel_id, posted_at, text, payload)
    VALUES (:mid, :etype, :seq, 7, 55, now(), :text, '{}')
"""


async def test_duplicate_event_inserted_once(db_session: AsyncSession) -> None:
    row = {
        "message_id": 1001,
        "event_type": "create",
        "event_seq": 0,
        "provider_id": 7,
        "channel_id": 55,
        "posted_at": T0,
        "text": "Bought GOOGL",
        "payload": {},
    }
    for _ in range(2):
        await db_session.execute(insert(MessageEvent).values(**row).on_conflict_do_nothing())
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(MessageEvent)) == 1


async def test_orm_defaults(db_session: AsyncSession) -> None:
    event = await add_message(db_session)
    await db_session.refresh(event)
    assert event.mentioned_author_ids == []
    assert event.backfilled is False
    assert event.received_at is not None


async def test_message_event_checks(db_session: AsyncSession) -> None:
    bad = [
        {"mid": 1, "etype": "update", "seq": 0, "text": "x"},  # unknown event type
        {"mid": 2, "etype": "create", "seq": -1, "text": "x"},  # negative seq
        {"mid": 3, "etype": "create", "seq": 0, "text": None},  # create without text
        {"mid": 0, "etype": "create", "seq": 0, "text": "x"},  # snowflake must be > 0
    ]
    for params in bad:
        await expect_error(db_session, errors.CheckViolation, EVENT_SQL, params)
    # delete events carry no text
    await db_session.execute(text(EVENT_SQL), {"mid": 4, "etype": "delete", "seq": 0, "text": None})
    await db_session.commit()


async def test_inbox_checks(db_session: AsyncSession) -> None:
    event = await add_message(db_session)
    sql = """
        INSERT INTO inbox (message_id, event_type, event_seq, provider_id, posted_at, status,
                           attempts)
        VALUES (1001, 'create', 0, 7, now(), :status, :attempts)
    """
    await expect_error(db_session, errors.CheckViolation, sql, {"status": "new", "attempts": 0})
    await expect_error(
        db_session, errors.CheckViolation, sql, {"status": "received", "attempts": -1}
    )
    item = await add_inbox(db_session, event)
    assert item.attempts == 0


async def test_inbox_requires_message_event(db_session: AsyncSession) -> None:
    await expect_error(
        db_session,
        errors.ForeignKeyViolation,
        """INSERT INTO inbox (message_id, event_type, event_seq, provider_id, posted_at, status)
           VALUES (999, 'create', 0, 7, now(), 'received')""",
    )


async def test_app_role_privileges(db_session: AsyncSession) -> None:
    event = await add_message(db_session)
    await add_inbox(db_session, event)
    await expect_error(
        db_session, errors.InsufficientPrivilege, "UPDATE message_events SET text = 'changed'"
    )
    await expect_error(db_session, errors.InsufficientPrivilege, "DELETE FROM signals")
    await expect_error(db_session, errors.InsufficientPrivilege, "DELETE FROM inbox")
    await db_session.execute(text("UPDATE inbox SET status = 'processing', attempts = 1"))
    await db_session.commit()


async def test_audit_identity_insert_as_app(db_session: AsyncSession) -> None:
    call = ModelCall(
        provider="anthropic",
        model="claude-sonnet-5",
        role="extract",
        attempt=1,
        request={"text": "Bought GOOGL"},
        outcome="ok",
        latency_ms=812,
        started_at=T0,
    )
    db_session.add(call)
    await db_session.commit()
    assert call.id == 1


async def _signal(session: AsyncSession, provider_id: int = 7) -> Signal:
    await add_message(session, provider_id=provider_id)
    signal = Signal(
        signal_id=uuid.uuid4(),
        message_id=1001,
        event_type="create",
        event_seq=0,
        idx=0,
        provider_id=provider_id,
        msg_type="entry",
        instrument="option",
        fields={},
        interpreter="llm",
        stale=False,
        late=False,
        schema_version=1,
    )
    session.add(signal)
    await session.commit()
    return signal


async def test_signal_unique_per_event_index(db_session: AsyncSession) -> None:
    signal = await _signal(db_session)
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO signals (signal_id, message_id, event_type, event_seq, idx, provider_id,
               msg_type, fields, interpreter, stale, late, schema_version)
           VALUES (gen_random_uuid(), 1001, 'create', 0, :idx, 7, 'entry', '{}', 'llm',
               false, false, 1)""",
        {"idx": signal.idx},
    )


async def test_message_link_position_iff_linked(db_session: AsyncSession) -> None:
    signal = await _signal(db_session)
    sql = """
        INSERT INTO message_links (signal_id, provider_id, outcome, method)
        VALUES (:sid, 7, :outcome, 'jev')
    """
    await expect_error(
        db_session, errors.CheckViolation, sql, {"sid": signal.signal_id, "outcome": "linked"}
    )
    link = MessageLink(
        signal_id=signal.signal_id,
        provider_id=7,
        outcome="ambiguous",
        method="jev",
        candidate_position_ids=[uuid.uuid4(), uuid.uuid4()],
    )
    db_session.add(link)
    await db_session.commit()


async def test_message_link_cannot_use_other_providers_signal(db_session: AsyncSession) -> None:
    signal = await _signal(db_session, provider_id=7)
    await expect_error(
        db_session,
        errors.ForeignKeyViolation,
        """INSERT INTO message_links (signal_id, provider_id, outcome, method)
           VALUES (:sid, 8, 'none', 'code')""",
        {"sid": signal.signal_id},
    )


async def test_next_for_provider_uses_partial_index(db_session: AsyncSession) -> None:
    await db_session.execute(text("SET enable_seqscan = off"))
    plan = "\n".join(
        (
            await db_session.execute(
                text("""
                    EXPLAIN SELECT message_id, event_type, event_seq FROM inbox
                    WHERE provider_id = 7 AND status IN ('received', 'processing')
                    ORDER BY posted_at, message_id LIMIT 1
                """)
            )
        ).scalars()
    )
    assert "ix_inbox_next_for_provider" in plan, plan
