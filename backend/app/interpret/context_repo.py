"""The reads behind C71 context, scoped to the lane (channel, provider): messages by post time
(latest text), the lane's open book. Never touches lots or anything user-portfolio (FR-8)."""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import col

from app.inbox.envelope import Lane
from app.inbox.models import EventType, MessageEvent
from app.interpret.models import Signal
from app.ledger.models import ProviderPosition, ProviderPositionEvent
from app.ledger.positions import LIVE, SETUPS
from app.markettime import et_day_start

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


def lane_book_filter(lane: Lane, as_of: datetime) -> ColumnElement[bool]:
    """The lane's book: live (open/trimmed) positions, plus the watch/conditional setups
    opened on `as_of`'s ET day (F072: a setup lasts one day). Opened in this channel by this
    provider; a position with no recorded channel belongs to no lane."""
    channel_id, provider_id = lane
    return and_(
        col(ProviderPosition.provider_id) == provider_id,
        col(ProviderPosition.opened_channel_id) == channel_id,
        or_(
            col(ProviderPosition.status).in_(list(LIVE)),
            and_(
                col(ProviderPosition.status).in_(list(SETUPS)),
                col(ProviderPosition.opened_at) >= et_day_start(as_of),
            ),
        ),
    )


async def open_book(db: AsyncSession, lane: Lane, as_of: datetime) -> list[ProviderPosition]:
    rows = await db.scalars(
        select(ProviderPosition)
        .where(lane_book_filter(lane, as_of))
        .order_by(col(ProviderPosition.opened_at), col(ProviderPosition.provider_position_id))
    )
    return list(rows.all())


async def recent_signal_events(
    db: AsyncSession, lane: Lane, before: datetime, limit: int
) -> list[tuple[ProviderPositionEvent, ProviderPosition, datetime]]:
    """F073: the lane's latest provider-position events caused by messages posted before
    `before` (newest first): what this provider recently did to which signal."""
    if limit <= 0:
        return []
    channel_id, provider_id = lane
    rows = await db.execute(
        select(ProviderPositionEvent, ProviderPosition, col(MessageEvent.posted_at))
        .join(
            ProviderPosition,
            col(ProviderPosition.provider_position_id)
            == col(ProviderPositionEvent.provider_position_id),
        )
        .join(Signal, col(Signal.signal_id) == col(ProviderPositionEvent.signal_id))
        .join(
            MessageEvent,
            and_(
                col(MessageEvent.message_id) == col(Signal.message_id),
                col(MessageEvent.event_type) == col(Signal.event_type),
                col(MessageEvent.event_seq) == col(Signal.event_seq),
            ),
        )
        .where(
            col(ProviderPosition.provider_id) == provider_id,
            col(ProviderPosition.opened_channel_id) == channel_id,
            col(MessageEvent.posted_at) < before,
        )
        .order_by(col(MessageEvent.posted_at).desc(), col(ProviderPositionEvent.id).desc())
        .limit(limit)
    )
    return [(e, p, at) for e, p, at in rows.tuples().all()]
