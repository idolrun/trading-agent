"""F024: RiskSnapshot from lots (foreign included), today's sell fills and live quotes."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, in_transaction
from app.decide.risk_snapshot import build_risk_snapshot
from app.decide.types import OpenLotBrief
from app.execution.models import AutoReason, Fill, Order
from app.instruments import OptionContract, Stock
from app.ledger import repo
from app.ledger.lots import apply_fill
from app.ledger.positions import apply_to_position
from app.marketdata.fake import FakeMarketData
from app.validate.normalize import NormalizedSignal
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_signal

D = Decimal
NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)  # 14:00 ET
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=D("357.5"), right="call"
)
AAPL = Stock(symbol="AAPL")
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def _sell_fill(
    db: AsyncSession, lot_id: uuid.UUID, qty: int, price: Decimal, at: datetime
) -> uuid.UUID:
    order = Order(
        client_order_id=f"t-{uuid.uuid4()}",
        auto_reason=AutoReason.EOD_FORCED_CLOSE,
        lot_id=lot_id,
        kind="exit",
        side="sell",
        instrument="option",
        broker_symbol=GOOGL.occ_symbol(),
        qty=qty,
        order_type="limit",
        limit_price=price,
        tif="day",
        status="filled",
        mode="shadow",
    )
    db.add(order)
    await db.flush()
    fill = Fill(
        order_id=order.order_id,
        broker_fill_id=f"f-{uuid.uuid4()}",
        qty=qty,
        price=price,
        filled_at=at,
    )
    db.add(fill)
    await db.flush()
    return fill.fill_id


async def test_snapshot(factory: Factory, db_session: AsyncSession) -> None:
    sig = await add_signal(db_session, provider_id=7)
    entry = NormalizedSignal(
        msg_type="entry",
        instrument="option",
        underlying="GOOGL",
        expiry=GOOGL.expiry,
        strike=GOOGL.strike,
        right="call",
    )
    async with in_transaction(factory, None) as db:
        opened = apply_to_position(None, entry)
        assert opened is not None
        position = await repo.record(
            db, provider_id=7, row=None, update=opened, signal_id=sig.signal_id
        )
        lot = await repo.record_lot(
            db,
            apply_fill(None, "buy", 3, D("1.00")),
            instrument=GOOGL,
            provider_position_id=position.provider_position_id,
            provider_id=7,
        )
        for qty, price, at in ((1, D("1.50"), NOW), (1, D("0.50"), NOW - timedelta(days=1))):
            fill_id = await _sell_fill(db, lot.lot_id, qty, price, at)
            update = apply_fill((lot.qty, lot.avg_price), "sell", qty, price)
            lot = await repo.record_lot(db, update, row=lot, fill_id=fill_id)
        await repo.create_foreign_lot(db, AAPL, 10, D("180"), reason="reconcile")

    md = FakeMarketData()
    md.set_quote(GOOGL, D("1.10"), D("1.30"))  # mid 1.20
    md.set_quote(AAPL, D("189"), D("191"))  # mid 190
    snap = await build_risk_snapshot(factory, md, NOW)
    assert snap.realized_today == D("50")  # only today's sell: (1.50 - 1.00) x 1 x 100
    assert snap.unrealized == D("120")  # 20 on the option + 100 on the foreign stock
    assert (snap.open_positions, snap.exposure) == (2, D("1900"))
    assert set(snap.lots) == {OpenLotBrief("GOOGL", "call"), OpenLotBrief("AAPL", None)}

    del md.quotes[AAPL.symbol]
    assert (await build_risk_snapshot(factory, md, NOW)).unrealized is None  # never 0
