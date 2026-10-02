"""F020: every ledger change writes exactly one history row; the open book is row-locked."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from psycopg import errors
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory, in_transaction
from app.interpret.models import LinkMethod, LinkOutcome, MessageLink
from app.ledger import repo
from app.ledger.models import ProviderPositionEvent
from app.ledger.positions import apply_to_position, state_of
from app.ledger.resolver import ParentLink
from app.validate.normalize import NormalizedSignal, Price
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_signal

NOW = datetime(2026, 9, 28, 14, 31, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def p(v: str) -> Price:
    return Price(raw=Decimal(v), value=Decimal(v), scale="as_is")


ENTRY = NormalizedSignal(
    msg_type="entry",
    instrument="option",
    underlying="GOOGL",
    expiry=date(2026, 10, 2),
    strike=Decimal("357.5"),
    right="call",
    entry_price=p("1.67"),
)


async def test_open_trim_exit_history(factory: Factory, db_session: AsyncSession) -> None:
    signal = await add_signal(db_session, provider_id=7)
    async with in_transaction(factory, None) as db:
        opened = apply_to_position(None, ENTRY)
        assert opened is not None
        row = await repo.record(
            db,
            provider_id=7,
            row=None,
            update=opened,
            signal_id=signal.signal_id,
            channel_id=55,
            at=NOW,
        )
        position_id = row.provider_position_id
    for msg_type, fraction in (("trim", "half"), ("full_exit", None)):
        async with in_transaction(factory, None) as db:
            [locked] = await repo.lock_open_book(db, (55, 7))
            update = apply_to_position(
                state_of(locked),
                NormalizedSignal(msg_type=msg_type, instrument=None, exit_fraction=fraction),
            )
            assert update is not None
            await repo.record(
                db, provider_id=7, row=locked, update=update, signal_id=signal.signal_id, at=NOW
            )

    events = (
        await db_session.scalars(
            select(ProviderPositionEvent)
            .where(ProviderPositionEvent.provider_position_id == position_id)
            .order_by(ProviderPositionEvent.id)
        )
    ).all()
    assert [e.change for e in events] == ["open", "trim", "exit"]
    assert events[0].before is None and events[0].after["provider_entry_price"] == "1.67"
    trim_before, trim_after = events[1].before, events[1].after
    assert trim_before is not None
    assert Decimal(trim_before["remaining_fraction"]) == 1
    assert Decimal(trim_after["remaining_fraction"]) == Decimal("0.5")
    async with factory() as db:
        assert await repo.lock_open_book(db, (55, 7)) == []  # closed positions leave the book


async def test_book_is_per_provider_and_locked(factory: Factory, db_session: AsyncSession) -> None:
    a = await add_signal(db_session, provider_id=7, message_id=1001)
    b = await add_signal(db_session, provider_id=8, message_id=1002)
    async with in_transaction(factory, None) as db:
        for provider, sig in ((7, a), (8, b)):
            update = apply_to_position(None, ENTRY)
            assert update is not None
            await repo.record(
                db,
                provider_id=provider,
                row=None,
                update=update,
                signal_id=sig.signal_id,
                channel_id=55,
                at=NOW,
            )

    async with factory() as holder, holder.begin():
        book = await repo.lock_open_book(holder, (55, 7))
        assert [r.provider_id for r in book] == [7]
        async with factory() as other, other.begin():
            with pytest.raises(DBAPIError) as exc:
                await other.execute(
                    text("SELECT 1 FROM provider_positions WHERE provider_id = 7 FOR UPDATE NOWAIT")
                )
            assert isinstance(exc.value.orig, errors.LockNotAvailable)
        async with factory() as other, other.begin():
            assert len(await repo.lock_open_book(other, (55, 8))) == 1  # other provider unaffected


async def test_book_is_per_lane(factory: Factory, db_session: AsyncSession) -> None:
    """F068: one provider, two channels = two books; a position without a channel is in none."""
    sig = await add_signal(db_session, provider_id=7)
    async with in_transaction(factory, None) as db:
        for channel in (55, 66, None):
            update = apply_to_position(None, ENTRY)
            assert update is not None
            await repo.record(
                db,
                provider_id=7,
                row=None,
                update=update,
                signal_id=sig.signal_id,
                channel_id=channel,
                at=NOW,
            )
    async with factory() as db:
        assert [r.opened_channel_id for r in await repo.lock_open_book(db, (55, 7))] == [55]
        assert [r.opened_channel_id for r in await repo.lock_open_book(db, (66, 7))] == [66]
        assert await repo.lock_open_book(db, (77, 7)) == []


async def test_recent_underlyings(factory: Factory, db_session: AsyncSession) -> None:
    sig = await add_signal(db_session, provider_id=7)
    async with in_transaction(factory, None) as db:
        update = apply_to_position(None, ENTRY)
        assert update is not None
        await repo.record(
            db, provider_id=7, row=None, update=update, signal_id=sig.signal_id, at=NOW
        )
    async with factory() as db:
        assert await repo.recent_underlyings(db, 7, NOW.replace(hour=0)) == ["GOOGL"]
        assert await repo.recent_underlyings(db, 8, NOW.replace(hour=0)) == []


async def test_parent_links_latest_row_per_signal(
    factory: Factory, db_session: AsyncSession
) -> None:
    """F021: the parent's current link = latest message_links row; unlinked rows are skipped."""
    sig = await add_signal(db_session, provider_id=7)
    async with in_transaction(factory, None) as db:
        first = apply_to_position(None, ENTRY)
        assert first is not None
        p1 = await repo.record(
            db, provider_id=7, row=None, update=first, signal_id=sig.signal_id, at=NOW
        )
        p2 = await repo.record(
            db, provider_id=7, row=None, update=first, signal_id=sig.signal_id, at=NOW
        )
        link = MessageLink(
            signal_id=sig.signal_id,
            provider_id=7,
            provider_position_id=p1.provider_position_id,
            outcome=LinkOutcome.NEW_POSITION,
            method=LinkMethod.CODE,
            confidence=Decimal(1),
        )
        db.add(link)
        await db.flush()
        db.add(
            MessageLink(
                signal_id=sig.signal_id,
                provider_id=7,
                provider_position_id=p2.provider_position_id,
                outcome=LinkOutcome.LINKED,
                method=LinkMethod.USER_CORRECTION,
                supersedes_link_id=link.id,
            )
        )
    async with factory() as db:
        assert await repo.parent_links(db, sig.message_id) == (
            ParentLink(7, p2.provider_position_id),
        )
        assert await repo.parent_links(db, 999) == ()
