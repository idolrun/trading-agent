"""Provider-position and lot queries. No ledger decisions here."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.execution.models import Fill
from app.inbox.models import MessageEvent
from app.instruments import Instrument as Contract
from app.instruments import OptionContract, broker_symbol
from app.interpret.models import LinkOutcome, MessageLink, MsgType, Signal
from app.ledger.correlation import ParentLink
from app.ledger.lots import LotUpdate
from app.ledger.models import (
    Instrument,
    Lot,
    LotChange,
    LotEvent,
    LotStatus,
    OptionRight,
    PositionStatus,
    ProviderPosition,
    ProviderPositionEvent,
)
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
    position_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> ProviderPosition:
    """Write one ledger change: insert/update the position and its history event together.
    Exactly one of `signal_id` / `manual_correction_id` (DB CHECK)."""
    now = at or datetime.now(UTC)
    values = update.after.model_dump()
    values["tp_levels"] = list(values["tp_levels"])
    if row is None:
        row = ProviderPosition(
            provider_position_id=position_id or uuid.uuid4(),  # deterministic from apply (F026)
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


async def open_lot_for_position(db: AsyncSession, provider_position_id: uuid.UUID) -> Lot | None:
    lot: Lot | None = await db.scalar(
        select(Lot).where(
            col(Lot.provider_position_id) == provider_position_id,
            col(Lot.status) == LotStatus.OPEN,
        )
    )
    return lot


async def lock_lot(db: AsyncSession, lot_id: uuid.UUID) -> Lot | None:
    lot: Lot | None = await db.scalar(
        select(Lot).where(col(Lot.lot_id) == lot_id).with_for_update()
    )
    return lot


async def open_lots(db: AsyncSession, underlying: str | None = None) -> list[Lot]:
    """All open lots incl. foreign (C47 counts toward limits); optionally one underlying."""
    query = select(Lot).where(col(Lot.status) == LotStatus.OPEN)
    if underlying is not None:
        query = query.where(col(Lot.underlying) == underlying)
    return list((await db.scalars(query.order_by(col(Lot.opened_at)))).all())


async def record_lot(
    db: AsyncSession,
    update: LotUpdate,
    *,
    row: Lot | None = None,
    instrument: Contract | None = None,
    provider_position_id: uuid.UUID | None = None,
    provider_id: int | None = None,
    fill_id: uuid.UUID | None = None,
    reason: str | None = None,
    at: datetime | None = None,
) -> Lot:
    """Write one lot change and its `lot_events` row. A new lot (`row=None`) needs the
    `instrument`; it is foreign exactly when it has no provider position (C47)."""
    now = at or datetime.now(UTC)
    if row is None:
        if instrument is None:
            raise ValueError("a new lot needs its instrument")
        option = instrument if isinstance(instrument, OptionContract) else None
        row = Lot(
            provider_position_id=provider_position_id,
            provider_id=provider_id,
            is_foreign=provider_position_id is None,
            instrument=Instrument.OPTION if option else Instrument.STOCK,
            broker_symbol=broker_symbol(instrument),
            underlying=option.underlying if option else broker_symbol(instrument),
            expiry=option.expiry if option else None,
            strike=option.strike if option else None,
            option_right=OptionRight(option.right) if option else None,
            qty=update.qty_after,
            avg_price=update.avg_after,
            status=update.status,
            opened_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.qty, row.avg_price, row.status, row.updated_at = (
            update.qty_after,
            update.avg_after,
            update.status,
            now,
        )
        if update.status == LotStatus.CLOSED:
            row.closed_at = now
    await db.flush()
    db.add(
        LotEvent(
            lot_id=row.lot_id,
            change=update.change,
            fill_id=fill_id,
            qty_before=update.qty_before,
            qty_after=update.qty_after,
            avg_before=update.avg_before,
            avg_after=update.avg_after,
            reason=reason,
            at=now,
        )
    )
    await db.flush()
    return row


async def create_foreign_lot(
    db: AsyncSession, instrument: Contract, qty: int, avg: Decimal | None, *, reason: str
) -> Lot:
    """Reconciliation only (F046): a broker position no system lot explains (read-only)."""
    update = LotUpdate(LotChange.ADOPT_FOREIGN, None, qty, None, avg)
    return await record_lot(db, update, instrument=instrument, reason=reason)


async def sells_since(
    db: AsyncSession, since: datetime
) -> list[tuple[Instrument, int, Decimal | None, Decimal]]:
    """Lot reductions by fills filled at/after `since`: (instrument, qty sold, avg before,
    fill price). Input for realized P&L (FR-18)."""
    rows = await db.execute(
        select(
            col(Lot.instrument),
            col(LotEvent.qty_before) - col(LotEvent.qty_after),
            col(LotEvent.avg_before),
            col(Fill.price),
        )
        .join(Fill, col(Fill.fill_id) == col(LotEvent.fill_id))
        .join(Lot, col(Lot.lot_id) == col(LotEvent.lot_id))
        .where(
            col(LotEvent.change).in_([LotChange.FILL_REDUCE, LotChange.CLOSE]),
            col(Fill.filled_at) >= since,
        )
    )
    return [(i, int(q), avg, price) for i, q, avg, price in rows.all()]


async def latest_link(db: AsyncSession, signal_id: uuid.UUID) -> MessageLink | None:
    """The current link of a signal (append-only table: latest row wins)."""
    link: MessageLink | None = await db.scalar(
        select(MessageLink)
        .where(col(MessageLink.signal_id) == signal_id)
        .order_by(col(MessageLink.id).desc())
        .limit(1)
    )
    return link


async def unlinked_follow_ups_after(
    db: AsyncSession, provider_id: int, posted_at: datetime, types: frozenset[MsgType]
) -> list[tuple[Signal, MessageEvent]]:
    """This provider's follow-ups posted after `posted_at` whose current link is none or
    ambiguous (F037 recheck), oldest first."""
    latest = (
        select(col(MessageLink.signal_id), col(MessageLink.outcome))
        .order_by(col(MessageLink.signal_id), col(MessageLink.id).desc())
        .distinct(col(MessageLink.signal_id))
        .subquery()
    )
    rows = await db.execute(
        select(Signal, MessageEvent)
        .join(
            MessageEvent,
            and_(
                col(MessageEvent.message_id) == col(Signal.message_id),
                col(MessageEvent.event_type) == col(Signal.event_type),
                col(MessageEvent.event_seq) == col(Signal.event_seq),
            ),
        )
        .join(latest, latest.c.signal_id == col(Signal.signal_id))
        .where(
            col(Signal.provider_id) == provider_id,
            col(MessageEvent.posted_at) > posted_at,
            col(Signal.msg_type).in_(list(types)),
            latest.c.outcome.in_([LinkOutcome.NONE, LinkOutcome.AMBIGUOUS]),
        )
        .order_by(col(MessageEvent.posted_at), col(Signal.idx))
    )
    return [(sig, event) for sig, event in rows.tuples().all()]


async def has_lot(db: AsyncSession, provider_position_id: uuid.UUID) -> bool:
    """Did the user ever hold a lot on this provider position (any status)?"""
    found = await db.scalar(
        select(col(Lot.lot_id))
        .where(col(Lot.provider_position_id) == provider_position_id)
        .limit(1)
    )
    return found is not None
