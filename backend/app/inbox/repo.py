"""Inbox transport boundary (TRD §2.4). Callers own the transaction."""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db import notify
from app.inbox.envelope import Envelope, EventKey
from app.inbox.models import (
    EventType,
    InboxItem,
    InboxStatus,
    MessageEvent,
    SkipReason,
    Stage,
    StageTiming,
)
from app.observability.counters import bump

_TERMINAL = (InboxStatus.DONE, InboxStatus.FAILED, InboxStatus.STALE)
_APPLIED = (InboxStatus.DONE, InboxStatus.STALE)


def _key_filter(model: type[InboxItem] | type[MessageEvent], key: EventKey) -> ColumnElement[bool]:
    return and_(
        col(model.message_id) == key.message_id,
        col(model.event_type) == key.event_type,
        col(model.event_seq) == key.event_seq,
    )


async def store_event(db: AsyncSession, env: Envelope) -> bool:
    """Store the event exactly once (no queueing). False = duplicate (no-op)."""
    inserted = await db.scalar(
        insert(MessageEvent)
        .values(
            message_id=env.key.message_id,
            event_type=env.key.event_type,
            event_seq=env.key.event_seq,
            provider_id=env.provider_id,
            channel_id=env.channel_id,
            posted_at=env.posted_at,
            edited_at=env.edited_at,
            reply_to_id=env.reply_to_id,
            mentioned_author_ids=list(env.mentioned_author_ids),
            text=env.text,
            payload=env.payload,
            backfilled=env.backfilled,
            received_at=env.received_at,
        )
        .on_conflict_do_nothing()
        .returning(col(MessageEvent.message_id))
    )
    return inserted is not None


async def accept(db: AsyncSession, env: Envelope) -> bool:
    """T1: store the event exactly once + queue it + NOTIFY. False = duplicate (no-op)."""
    if not await store_event(db, env):
        bump("duplicate")
        return False
    message_id, event_type, event_seq = env.key
    db.add(
        InboxItem(
            message_id=message_id,
            event_type=event_type,
            event_seq=event_seq,
            provider_id=env.provider_id,
            posted_at=env.posted_at,
            status=InboxStatus.RECEIVED,
            backfilled=env.backfilled,
        )
    )
    db.add(
        StageTiming(
            message_id=message_id,
            event_type=event_type,
            event_seq=event_seq,
            attempt=0,
            stage=Stage.RECEIVED,
            at=env.received_at,
        )
    )
    await db.flush()
    await notify(db, "inbox", str(env.provider_id))
    await notify(db, "feed", json.dumps({"type": "feed.message", "id": str(message_id)}))  # F056
    return True


async def next_for_provider(db: AsyncSession, provider_id: int) -> Envelope | None:
    """The provider's earliest waiting event in post-time (snowflake) order."""
    row = await db.scalar(
        select(MessageEvent)
        .join(
            InboxItem,
            and_(
                col(InboxItem.message_id) == col(MessageEvent.message_id),
                col(InboxItem.event_type) == col(MessageEvent.event_type),
                col(InboxItem.event_seq) == col(MessageEvent.event_seq),
            ),
        )
        .where(
            col(InboxItem.provider_id) == provider_id,
            col(InboxItem.status) == InboxStatus.RECEIVED,
        )
        .order_by(
            col(InboxItem.posted_at),
            col(InboxItem.message_id),
            col(InboxItem.event_type),
            col(InboxItem.event_seq),
        )
        .limit(1)
    )
    return None if row is None else to_envelope_row(row)


def to_envelope_row(row: MessageEvent) -> Envelope:
    return Envelope(
        key=EventKey(row.message_id, EventType(row.event_type), row.event_seq),
        provider_id=row.provider_id,
        channel_id=row.channel_id,
        posted_at=row.posted_at,
        received_at=row.received_at,
        reply_to_id=row.reply_to_id,
        mentioned_author_ids=tuple(row.mentioned_author_ids),
        text=row.text,
        edited_at=row.edited_at,
        backfilled=row.backfilled,
        payload=row.payload,
    )


async def claim(db: AsyncSession, key: EventKey, now: datetime) -> int:
    """T2: mark processing, count the attempt, record `dequeued`. Returns the attempt number."""
    attempts: int | None = await db.scalar(
        update(InboxItem)
        .where(_key_filter(InboxItem, key))
        .values(
            status=InboxStatus.PROCESSING,
            attempts=col(InboxItem.attempts) + 1,
            claimed_at=now,
            updated_at=now,
        )
        .returning(col(InboxItem.attempts))
    )
    if attempts is None:
        raise LookupError(f"no inbox row for {key}")
    db.add(StageTiming(**key._asdict(), attempt=attempts, stage=Stage.DEQUEUED, at=now))
    return attempts


async def mark(
    db: AsyncSession,
    key: EventKey,
    status: InboxStatus,
    *,
    error: str | None = None,
    skip_reason: SkipReason | None = None,
    late: bool | None = None,
) -> bool:
    """False = nothing changed: a retry or failure mark (`received`/`failed`) never rewinds an
    applied (`done`/`stale`) event; its cards and ledger changes are committed."""
    values: dict[str, object] = {"status": status, "updated_at": func.now()}
    if error is not None:
        values["last_error"] = error
    if skip_reason is not None:
        values["skip_reason"] = skip_reason
    if late is not None:
        values["late"] = late
    if status in _TERMINAL:
        values["finished_at"] = func.now()
    where = _key_filter(InboxItem, key)
    if status in (InboxStatus.RECEIVED, InboxStatus.FAILED):
        where = and_(where, col(InboxItem.status).not_in(_APPLIED))
    result = await db.execute(update(InboxItem).where(where).values(**values))
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def reset_in_flight(db: AsyncSession) -> int:
    """Startup (TRD §9 step 6): events left `processing` by a crash go back to `received`."""
    result = await db.execute(
        update(InboxItem)
        .where(col(InboxItem.status) == InboxStatus.PROCESSING)
        .values(status=InboxStatus.RECEIVED, updated_at=func.now())
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def providers_waiting(db: AsyncSession) -> list[int]:
    rows = await db.scalars(
        select(col(InboxItem.provider_id))
        .where(col(InboxItem.status) == InboxStatus.RECEIVED)
        .distinct()
    )
    return list(rows.all())


async def later_applied(db: AsyncSession, provider_id: int, posted_at: datetime) -> bool:
    """FR-1b: was a later-posted event of this provider already applied?"""
    found = await db.scalar(
        select(
            exists().where(
                col(InboxItem.provider_id) == provider_id,
                col(InboxItem.status).in_(_APPLIED),
                col(InboxItem.posted_at) > posted_at,
            )
        )
    )
    return bool(found)


@dataclass(frozen=True)
class Backlog:
    count: int
    oldest_posted_at: datetime | None


async def backlog(db: AsyncSession) -> Backlog:
    """Events waiting to be processed (FR-1c catch-up; uses the `received` partial index)."""
    count, oldest = (
        await db.execute(
            select(func.count(), func.min(col(InboxItem.posted_at))).where(
                col(InboxItem.status) == InboxStatus.RECEIVED
            )
        )
    ).one()
    return Backlog(count=count, oldest_posted_at=oldest)


async def last_seen_per_channel(
    db: AsyncSession, channels: list[int], posted_before: datetime
) -> dict[int, int]:
    """Newest create-event message id per channel posted before `posted_before`."""
    rows = await db.execute(
        select(col(MessageEvent.channel_id), func.max(col(MessageEvent.message_id)))
        .where(
            col(MessageEvent.channel_id).in_(channels),
            col(MessageEvent.event_type) == EventType.CREATE,
            col(MessageEvent.posted_at) < posted_before,
        )
        .group_by(col(MessageEvent.channel_id))
    )
    return {channel: message_id for channel, message_id in rows.all()}
