"""Shared helpers for schema constraint tests."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import errors
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, User
from app.inbox.models import InboxItem, InboxStatus, MessageEvent
from app.interpret.models import Signal

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


async def expect_error(
    session: AsyncSession,
    error: type[errors.Error],
    sql: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Run `sql` in a savepoint; assert the driver raised `error`; the session stays usable."""
    with pytest.raises(DBAPIError) as exc:
        async with session.begin_nested():
            await session.execute(text(sql), params or {})
    assert isinstance(exc.value.orig, error), exc.value.orig


async def add_message(
    session: AsyncSession, message_id: int = 1001, provider_id: int = 7, **overrides: Any
) -> MessageEvent:
    event = MessageEvent(
        message_id=message_id,
        event_type=overrides.pop("event_type", "create"),
        event_seq=overrides.pop("event_seq", 0),
        provider_id=provider_id,
        channel_id=overrides.pop("channel_id", 55),
        posted_at=overrides.pop("posted_at", T0),
        text=overrides.pop("text", "Bought GOOGL 357.5C @ 1.67"),
        payload=overrides.pop("payload", {"raw": True}),
        **overrides,
    )
    session.add(event)
    await session.commit()
    return event


async def add_inbox(session: AsyncSession, event: MessageEvent, **overrides: Any) -> InboxItem:
    item = InboxItem(
        message_id=event.message_id,
        event_type=event.event_type,
        event_seq=event.event_seq,
        provider_id=event.provider_id,
        channel_id=event.channel_id,
        posted_at=event.posted_at,
        status=overrides.pop("status", InboxStatus.RECEIVED),
        **overrides,
    )
    session.add(item)
    await session.commit()
    return item


async def add_session(session: AsyncSession) -> uuid.UUID:
    """A user + auth session; returns `session_id` (FK target of audit rows)."""
    user = User(email="owner@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    auth = AuthSession(
        token_hash=b"hash-1", user_id=user.user_id, expires_at=T0 + timedelta(hours=1)
    )
    session.add(auth)
    await session.commit()
    return auth.session_id


async def add_signal(session: AsyncSession, provider_id: int = 7, message_id: int = 1001) -> Signal:
    """A message event plus one entry signal from `provider_id`."""
    await add_message(session, message_id=message_id, provider_id=provider_id)
    signal = Signal(
        signal_id=uuid.uuid4(),
        message_id=message_id,
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
