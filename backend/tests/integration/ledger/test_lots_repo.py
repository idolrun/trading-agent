"""F022: lots are one open per provider position; every change writes lot_events."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, in_transaction
from app.instruments import OptionContract, Stock
from app.ledger import repo
from app.ledger.lots import apply_fill
from app.ledger.models import LotEvent
from app.ledger.positions import apply_to_position
from app.validate.normalize import NormalizedSignal
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_signal

NOW = datetime(2026, 9, 28, 14, 31, tzinfo=UTC)
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
ENTRY = NormalizedSignal(
    msg_type="entry",
    instrument="option",
    underlying="GOOGL",
    expiry=GOOGL.expiry,
    strike=GOOGL.strike,
    right="call",
)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def _position(
    factory: Factory, db_session: AsyncSession, provider_id: int, msg: int
) -> uuid.UUID:
    sig = await add_signal(db_session, provider_id=provider_id, message_id=msg)
    async with in_transaction(factory, None) as db:
        update = apply_to_position(None, ENTRY)
        assert update is not None
        row = await repo.record(
            db, provider_id=provider_id, row=None, update=update, signal_id=sig.signal_id, at=NOW
        )
        return row.provider_position_id


async def test_reentry_after_sell_all_is_new_lot(
    factory: Factory, db_session: AsyncSession
) -> None:
    pp = await _position(factory, db_session, 7, 1001)
    async with in_transaction(factory, None) as db:
        lot = await repo.record_lot(
            db,
            apply_fill(None, "buy", 2, Decimal("1")),
            instrument=GOOGL,
            provider_position_id=pp,
            provider_id=7,
        )
        first_id = lot.lot_id
        assert lot.broker_symbol == GOOGL.occ_symbol() and not lot.is_foreign
    async with in_transaction(factory, None) as db:
        found = await repo.open_lot_for_position(db, pp)
        assert found is not None and found.lot_id == first_id
        locked = await repo.lock_lot(db, first_id)
        assert locked is not None
        await repo.record_lot(
            db, apply_fill((2, locked.avg_price), "sell", 2, Decimal("2")), row=locked
        )
    async with in_transaction(factory, None) as db:
        assert await repo.open_lot_for_position(db, pp) is None
        again = await repo.record_lot(
            db,
            apply_fill(None, "buy", 1, Decimal("1.2")),
            instrument=GOOGL,
            provider_position_id=pp,
            provider_id=7,
        )
        assert again.lot_id != first_id
    changes = (
        await db_session.scalars(
            select(LotEvent.change).where(LotEvent.lot_id == first_id).order_by(LotEvent.id)
        )
    ).all()
    assert changes == ["open", "close"]


async def test_two_providers_same_contract_two_lots_plus_foreign(
    factory: Factory, db_session: AsyncSession
) -> None:
    pa = await _position(factory, db_session, 7, 1001)
    pb = await _position(factory, db_session, 8, 1002)
    async with in_transaction(factory, None) as db:
        for pp, provider in ((pa, 7), (pb, 8)):
            await repo.record_lot(
                db,
                apply_fill(None, "buy", 1, Decimal("1")),
                instrument=GOOGL,
                provider_position_id=pp,
                provider_id=provider,
            )
        foreign = await repo.create_foreign_lot(
            db, Stock(symbol="GOOGL"), 10, None, reason="reconcile: unmatched broker position"
        )
        assert foreign.is_foreign and foreign.provider_position_id is None
    async with factory() as db:
        lots = await repo.open_lots(db, "GOOGL")
        assert len(lots) == 3
        assert sorted(str(lot.provider_id) for lot in lots) == ["7", "8", "None"]
        assert await repo.open_lots(db, "META") == []
