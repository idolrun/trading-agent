"""Provider-position and lot queries. No ledger decisions here."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.interpret.models import MessageLink, Signal
from app.ledger.correlation import ParentLink
from app.ledger.models import PositionStatus, ProviderPosition, ProviderPositionEvent
from app.ledger.positions import PositionUpdate


async def recent_underlyings(db: AsyncSession, provider_id: int, since: datetime) -> list[str]:
    """Underlyings this provider opened positions in since `since` (C84 suggestion input)."""
    rows = await db.scalars(
        select(col(ProviderPosition.underlying))
        .where(
            col(ProviderPosition.provider_id) == provider_id,
            col(ProviderPosition.opened_at) >= since,
        )
        .distinct()
        .order_by(col(ProviderPosition.underlying))
    )
    return list(rows.all())


async def parent_links(db: AsyncSession, parent_message_id: int) -> tuple[ParentLink, ...]:
    """Current links (latest `message_links` row per signal) of a message's signals, any
    author and any event of that message; only rows that point at a position."""
    latest = (
        select(MessageLink)
        .join(Signal, col(Signal.signal_id) == col(MessageLink.signal_id))
        .where(col(Signal.message_id) == parent_message_id)
        .order_by(col(MessageLink.signal_id), col(MessageLink.id).desc())
        .distinct(col(MessageLink.signal_id))
        .subquery()
    )
    rows = await db.execute(
        select(latest.c.provider_id, latest.c.provider_position_id).where(
            latest.c.provider_position_id.is_not(None)
        )
    )
    return tuple(ParentLink(provider_id, position_id) for provider_id, position_id in rows.all())


async def lock_open_book(db: AsyncSession, provider_id: int) -> list[ProviderPosition]:
    """The provider's open/trimmed positions, row-locked for the rest of the transaction
    (T3/T7, database-schema.md §8). Call only inside the provider slot."""
    rows = await db.scalars(
        select(ProviderPosition)
        .where(
            col(ProviderPosition.provider_id) == provider_id,
            col(ProviderPosition.status) != PositionStatus.CLOSED,
        )
        .order_by(col(ProviderPosition.opened_at), col(ProviderPosition.provider_position_id))
        .with_for_update()
    )
    return list(rows.all())


async def record(
    db: AsyncSession,
    *,
    provider_id: int,
    row: ProviderPosition | None,
    update: PositionUpdate,
    signal_id: uuid.UUID | None = None,
    manual_correction_id: int | None = None,
    channel_id: int | None = None,
    at: datetime | None = None,
) -> ProviderPosition:
    """Write one ledger change: insert/update the position and its history event together.
    Exactly one of `signal_id` / `manual_correction_id` (DB CHECK)."""
    now = at or datetime.now(UTC)
    values = update.after.model_dump()
    values["tp_levels"] = list(values["tp_levels"])
    if row is None:
        row = ProviderPosition(
            provider_id=provider_id,
            opened_channel_id=channel_id,
            opened_by_signal_id=signal_id,
            opened_at=now,
            updated_at=now,
            **values,
        )
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = now
        if update.after.status == PositionStatus.CLOSED and row.closed_at is None:
            row.closed_at = now
    await db.flush()
    after = update.after.model_dump(mode="json")
    if update.add_price is not None:
        after["add_price"] = str(update.add_price)
    db.add(
        ProviderPositionEvent(
            provider_position_id=row.provider_position_id,
            change=update.change,
            signal_id=signal_id,
            manual_correction_id=manual_correction_id,
            before=update.before.model_dump(mode="json") if update.before else None,
            after=after,
            at=now,
        )
    )
    await db.flush()
    return row
