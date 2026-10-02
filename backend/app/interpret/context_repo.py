"""The reads behind C71 context, scoped to the lane (channel, provider): messages by post time
(latest text), the lane's open book. Never touches lots or anything user-portfolio (FR-8)."""

from datetime import datetime

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import col

from app.inbox.envelope import Lane
from app.inbox.models import EventType, MessageEvent
from app.ledger.models import PositionStatus, ProviderPosition

_deleted = aliased(MessageEvent)


def _not_deleted_before(before: datetime) -> ColumnElement[bool]:
    return ~exists().where(
        col(_deleted.message_id) == col(MessageEvent.message_id),
        col(_deleted.event_type) == EventType.DELETE,
        col(_deleted.posted_at) < before,
    )


async def _latest_texts(
    db: AsyncSession, message_ids: list[int], before: datetime
) -> dict[int, str]:
    """Newest edit text per message as of `before` (edits are sequenced at edit time)."""
    rows = await db.execute(
        select(col(MessageEvent.message_id), col(MessageEvent.text))
        .where(
            col(MessageEvent.message_id).in_(message_ids),
            col(MessageEvent.event_type) == EventType.EDIT,
            col(MessageEvent.posted_at) < before,
        )
        .order_by(col(MessageEvent.posted_at))
    )
    return {message_id: text for message_id, text in rows.all() if text is not None}


async def message_as_of(
    db: AsyncSession, message_id: int, before: datetime
) -> tuple[MessageEvent, str] | None:
    """A message (any author) with its latest text as of `before`; None if unknown/deleted."""
    create = await db.scalar(
        select(MessageEvent).where(
            col(MessageEvent.message_id) == message_id,
            col(MessageEvent.event_type) == EventType.CREATE,
            _not_deleted_before(before),
        )
    )
    if create is None or create.text is None:
        return None
    edits = await _latest_texts(db, [message_id], before)
    return create, edits.get(message_id, create.text)


async def recent_by_author(
    db: AsyncSession, lane: Lane, before: datetime, exclude_message_id: int, limit: int
) -> list[tuple[MessageEvent, str]]:
    """The lane author's newest `limit` messages in the lane's channel (threads included)
    posted before `before`, oldest first. Other channels are other lanes (F068)."""
    if limit <= 0:
        return []
    channel_id, author_id = lane
    creates = list(
        (
            await db.scalars(
                select(MessageEvent)
                .where(
                    col(MessageEvent.provider_id) == author_id,
                    col(MessageEvent.channel_id) == channel_id,
                    col(MessageEvent.event_type) == EventType.CREATE,
                    col(MessageEvent.posted_at) < before,
                    col(MessageEvent.message_id) != exclude_message_id,
                    _not_deleted_before(before),
                )
                .order_by(col(MessageEvent.posted_at).desc(), col(MessageEvent.message_id).desc())
                .limit(limit)
            )
        ).all()
    )
    edits = await _latest_texts(db, [c.message_id for c in creates], before)
    return [(c, edits.get(c.message_id, c.text or "")) for c in reversed(creates)]


async def open_book(db: AsyncSession, lane: Lane) -> list[ProviderPosition]:
    """The lane's open/trimmed positions: opened in this channel by this provider. A position
    with no recorded channel belongs to no lane."""
    channel_id, provider_id = lane
    rows = await db.scalars(
        select(ProviderPosition)
        .where(
            col(ProviderPosition.provider_id) == provider_id,
            col(ProviderPosition.opened_channel_id) == channel_id,
            col(ProviderPosition.status) != PositionStatus.CLOSED,
        )
        .order_by(col(ProviderPosition.opened_at), col(ProviderPosition.provider_position_id))
    )
    return list(rows.all())
