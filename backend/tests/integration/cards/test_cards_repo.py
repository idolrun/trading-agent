"""F025: cards are inserted once, every transition writes card_events, races have one winner."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.cards import repo
from app.cards.models import (
    Card,
    CardAction,
    CardActor,
    CardEvent,
    CardReason,
    CardSource,
    CardState,
    CardType,
)
from app.cards.state import IllegalTransition
from app.db import create_session_factory, in_transaction
from app.ledger import repo as ledger
from app.ledger.positions import apply_to_position
from app.validate.normalize import NormalizedSignal
from tests.integration.conftest import _url
from tests.integration.schema.helpers import add_signal

POSTED = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def _setup(factory: Factory, db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    sig = await add_signal(db_session, provider_id=7)
    entry = NormalizedSignal(
        msg_type="entry",
        instrument="option",
        underlying="GOOGL",
        expiry=date(2026, 10, 2),
        strike=Decimal("357.5"),
        right="call",
    )
    async with in_transaction(factory, None) as db:
        update = apply_to_position(None, entry)
        assert update is not None
        row = await ledger.record(
            db, provider_id=7, row=None, update=update, signal_id=sig.signal_id
        )
    return sig.signal_id, row.provider_position_id


def _card(signal_id: uuid.UUID, position_id: uuid.UUID, action: CardAction) -> Card:
    return Card(
        dedupe_key=f"{signal_id}:{action}",
        source=CardSource.SIGNAL,
        signal_id=signal_id,
        action=action,
        card_type=CardType.NORMAL,
        state=CardState.PENDING,
        provider_id=7,
        provider_position_id=position_id,
        proposal={"action": action, "qty": 5},
        posted_at=POSTED,
        valid_until=POSTED + timedelta(seconds=30),
    )


async def test_insert_once_and_transition_history(
    factory: Factory, db_session: AsyncSession
) -> None:
    signal_id, position_id = await _setup(factory, db_session)
    async with in_transaction(factory, None) as db:
        assert await repo.insert(db, _card(signal_id, position_id, CardAction.ENTRY))
    async with in_transaction(factory, None) as db:  # re-processing the same event
        assert not await repo.insert(db, _card(signal_id, position_id, CardAction.ENTRY))
    card_id = repo.card_id_for(f"{signal_id}:{CardAction.ENTRY}")

    async with in_transaction(factory, None) as db:
        card = await repo.lock(db, card_id)
        assert card is not None
        await repo.transition(
            db,
            card,
            CardState.PENDING,
            CardReason.EDITED,
            CardActor.USER,
            proposal={"action": "entry", "qty": 3},
        )
        await repo.transition(db, card, CardState.APPROVED, CardReason.APPROVED, CardActor.USER)
        with pytest.raises(IllegalTransition):
            await repo.transition(db, card, CardState.EXPIRED, CardReason.TIMEOUT, CardActor.TIMER)

    stored = await db_session.get(Card, card_id)
    assert stored is not None and (stored.state, stored.version) == (CardState.APPROVED, 2)
    events = (
        await db_session.scalars(
            select(CardEvent).where(CardEvent.card_id == card_id).order_by(CardEvent.id)
        )
    ).all()
    assert [(e.from_state, e.to_state, e.reason_code) for e in events] == [
        (None, "pending", "created"),
        ("pending", "pending", "edited"),
        ("pending", "approved", "approved"),
    ]


async def test_approve_vs_expire_one_winner(factory: Factory, db_session: AsyncSession) -> None:
    signal_id, position_id = await _setup(factory, db_session)
    async with in_transaction(factory, None) as db:
        await repo.insert(db, _card(signal_id, position_id, CardAction.ENTRY))
    card_id = repo.card_id_for(f"{signal_id}:{CardAction.ENTRY}")

    async def attempt(to: CardState, reason: CardReason, actor: CardActor) -> bool:
        try:
            async with in_transaction(factory, None) as db:
                card = await repo.lock(db, card_id)
                assert card is not None
                await asyncio.sleep(0.05)  # hold the lock so the other attempt waits
                await repo.transition(db, card, to, reason, actor)
            return True
        except IllegalTransition:
            return False

    results = await asyncio.gather(
        attempt(CardState.APPROVED, CardReason.APPROVED, CardActor.USER),
        attempt(CardState.EXPIRED, CardReason.TIMEOUT, CardActor.TIMER),
    )
    assert sorted(results) == [False, True]
    count = await db_session.scalar(
        select(func.count()).select_from(CardEvent).where(col(CardEvent.card_id) == card_id)
    )
    assert count == 2  # created + exactly one winner


async def test_pending_on_position_for_supersession(
    factory: Factory, db_session: AsyncSession
) -> None:
    signal_id, position_id = await _setup(factory, db_session)
    async with in_transaction(factory, None) as db:
        for action in (CardAction.ENTRY, CardAction.EXIT):
            await repo.insert(db, _card(signal_id, position_id, action))
    async with in_transaction(factory, None) as db:
        found = await repo.pending_on_position(db, position_id, [CardAction.ENTRY, CardAction.ADD])
        assert [c.action for c in found] == [CardAction.ENTRY]
        await repo.transition(
            db, found[0], CardState.INVALIDATED, CardReason.SUPERSEDED, CardActor.SYSTEM
        )
    async with in_transaction(factory, None) as db:
        assert await repo.pending_on_position(db, position_id, [CardAction.ENTRY]) == []
