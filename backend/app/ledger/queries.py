"""Read side of the ledgers (FR-25): provider positions and lots. No writes."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.api.schemas.cards import ContractOut
from app.api.schemas.ledger import (
    HistoryOut,
    LinkOut,
    LotDetail,
    LotEventOut,
    LotOut,
    PositionDetail,
    PositionOut,
    StopRowOut,
)
from app.execution import queries as orders
from app.execution.models import Stop
from app.interpret.models import MessageLink
from app.ledger.models import Lot, LotEvent, ProviderPosition, ProviderPositionEvent


def position_out(p: ProviderPosition) -> PositionOut:
    return PositionOut(
        id=p.provider_position_id,
        provider_id=p.provider_id,
        opened_channel_id=p.opened_channel_id,
        instrument=p.instrument,
        underlying=p.underlying,
        expiry=p.expiry,
        strike=p.strike,
        option_right=p.option_right,
        status=p.status,
        is_corrected=p.is_corrected,
        provider_entry_price=p.provider_entry_price,
        remaining_fraction=p.remaining_fraction,
        stop_level=p.stop_level,
        stop_basis=p.stop_basis,
        tp_levels=list(p.tp_levels),
        opened_at=p.opened_at,
    )


def lot_out(lot: Lot) -> LotOut:
    return LotOut(
        id=lot.lot_id,
        provider_position_id=lot.provider_position_id,
        provider_id=lot.provider_id,
        contract=ContractOut(
            instrument=lot.instrument,
            underlying=lot.underlying,
            expiry=lot.expiry,
            strike=lot.strike,
            right=lot.option_right,
        ),
        qty=lot.qty,
        avg_price=lot.avg_price,
        is_foreign=lot.is_foreign,
        status=lot.status,
        opened_at=lot.opened_at,
    )


async def list_positions(
    db: AsyncSession, *, status: str | None, provider_id: int | None, cursor: str | None, limit: int
) -> tuple[list[PositionOut], str | None]:
    query = select(ProviderPosition)
    if status is not None:
        query = query.where(col(ProviderPosition.status) == status)
    if provider_id is not None:
        query = query.where(col(ProviderPosition.provider_id) == provider_id)
    at, key = col(ProviderPosition.opened_at), col(ProviderPosition.provider_position_id)
    rows = (await db.scalars(keyset(query, at, key, cursor, limit, uuid.UUID))).all()
    page, nxt = cut(rows, limit, lambda p: (p.opened_at, p.provider_position_id))
    return [position_out(p) for p in page], nxt


async def get_position(db: AsyncSession, position_id: uuid.UUID) -> PositionDetail | None:
    p = await db.get(ProviderPosition, position_id)
    if p is None:
        return None
    events = await db.scalars(
        select(ProviderPositionEvent)
        .where(col(ProviderPositionEvent.provider_position_id) == position_id)
        .order_by(col(ProviderPositionEvent.id))
    )
    lots = await db.scalars(
        select(Lot).where(col(Lot.provider_position_id) == position_id).order_by(col(Lot.opened_at))
    )
    links = await db.scalars(
        select(MessageLink)
        .where(col(MessageLink.provider_position_id) == position_id)
        .order_by(col(MessageLink.id))
    )
    return PositionDetail(
        **position_out(p).model_dump(),
        events=[
            HistoryOut(change=e.change, before=e.before, after=e.after, at=e.at) for e in events
        ],
        lots=[lot_out(lot) for lot in lots],
        links=[
            LinkOut(
                signal_id=link.signal_id,
                outcome=link.outcome,
                method=link.method,
                confidence=link.confidence,
                created_at=link.created_at,
            )
            for link in links
        ],
    )


async def list_lots(
    db: AsyncSession, *, status: str | None, foreign: bool | None, cursor: str | None, limit: int
) -> tuple[list[LotOut], str | None]:
    query = select(Lot)
    if status is not None:
        query = query.where(col(Lot.status) == status)
    if foreign is not None:
        query = query.where(col(Lot.is_foreign) == foreign)
    rows = (
        await db.scalars(
            keyset(query, col(Lot.opened_at), col(Lot.lot_id), cursor, limit, uuid.UUID)
        )
    ).all()
    page, nxt = cut(rows, limit, lambda lot: (lot.opened_at, lot.lot_id))
    return [lot_out(lot) for lot in page], nxt


async def get_lot(db: AsyncSession, lot_id: uuid.UUID) -> LotDetail | None:
    lot = await db.get(Lot, lot_id)
    if lot is None:
        return None
    events = await db.scalars(
        select(LotEvent).where(col(LotEvent.lot_id) == lot_id).order_by(col(LotEvent.id))
    )
    stops = await db.scalars(
        select(Stop).where(col(Stop.lot_id) == lot_id).order_by(col(Stop.created_at))
    )
    return LotDetail(
        **lot_out(lot).model_dump(),
        events=[LotEventOut.model_validate(e, from_attributes=True) for e in events],
        stops=[
            StopRowOut(
                id=s.stop_id,
                kind=s.kind,
                basis=s.basis,
                level=s.level,
                qty=s.qty,
                held_by=s.held_by,
                status=s.status,
            )
            for s in stops
        ],
        orders=await orders.for_lot(db, lot_id),
    )
