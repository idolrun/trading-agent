"""F072: watch / conditional setups and re-entries through the message graph (real PG)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.cards.models import Card, CardAction, CardType
from app.db import create_session_factory
from app.inbox.envelope import Envelope
from app.interpret.signal import ParsedSignal, SignalFields
from app.ledger.models import (
    PositionChange,
    PositionStatus,
    ProviderPosition,
    ProviderPositionEvent,
)
from app.pipeline.apply import signal_id_for
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import T0, Factory, Harness, lit, opened_position
from tests.messages import PROVIDER_A

S = timedelta(seconds=1)
SPY_ENTRY = "SPY 580p 9/28 2.30"


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path)


async def positions(db: AsyncSession) -> list[ProviderPosition]:
    return list((await db.scalars(select(ProviderPosition))).all())


async def cards_of(db: AsyncSession, env: Envelope) -> list[Card]:
    return list(
        (await db.scalars(select(Card).where(Card.signal_id == signal_id_for(env.key, 0)))).all()
    )


async def test_watch_then_conditional_then_entry_is_one_signal(
    h: Harness, db_session: AsyncSession
) -> None:
    watch = await h.message(
        "watching SPY",
        [ParsedSignal(msg_type="watch", fields=SignalFields(underlying=lit("SPY")))],
        at=T0,
        author=PROVIDER_A,
    )
    cond = await h.message(
        "if SPY loses 580 then puts",
        [
            ParsedSignal(
                msg_type="conditional",
                instrument="option",
                fields=SignalFields(underlying=lit("SPY"), right=lit("put", "puts")),
            )
        ],
        at=T0 + S,
        author=PROVIDER_A,
    )
    for env in (watch, cond):
        await h.process(env)
    [setup] = await positions(db_session)
    assert (setup.status, setup.option_right, setup.instrument) == (
        PositionStatus.CONDITIONAL,
        "put",
        "option",
    )
    assert setup.condition_text == "if SPY loses 580 then puts"
    assert await cards_of(db_session, watch) == [] and await cards_of(db_session, cond) == []

    entry = await h.message(SPY_ENTRY, None, at=T0 + 10 * S, author=PROVIDER_A)
    await h.process(entry, processor=h.processor(fixture=False))
    [row] = await positions(db_session)
    await db_session.refresh(row)
    assert row.provider_position_id == setup.provider_position_id  # opened in place
    assert (row.status, row.strike, row.expiry is not None) == (
        PositionStatus.OPEN,
        Decimal(580),
        True,
    )
    [card] = await cards_of(db_session, entry)
    assert card.action == CardAction.ENTRY  # an entry, not an add
    changes = (
        await db_session.scalars(
            select(ProviderPositionEvent.change)
            .where(ProviderPositionEvent.provider_position_id == row.provider_position_id)
            .order_by(ProviderPositionEvent.id)
        )
    ).all()
    assert changes == [PositionChange.WATCH, PositionChange.CONDITION_SET, PositionChange.OPEN]


async def test_a_trim_never_acts_on_a_setup(h: Harness, db_session: AsyncSession) -> None:
    watch = await h.message(
        "watching SPY",
        [ParsedSignal(msg_type="watch", fields=SignalFields(underlying=lit("SPY")))],
        at=T0,
        author=PROVIDER_A,
    )
    await h.process(watch)
    trim = await h.message(
        "trim SPY half",
        None,
        at=T0 + 5 * S,
        author=PROVIDER_A,
        reply_to_id=str(watch.key.message_id),
    )
    await h.process(trim, processor=h.processor(fixture=False))
    [setup] = await positions(db_session)
    assert setup.status == PositionStatus.WATCH  # untouched: a setup is not a holding


async def test_back_in_re_enters_the_closed_position(h: Harness, db_session: AsyncSession) -> None:
    fast = h.processor(fixture=False)
    entry = await h.message(SPY_ENTRY, None, at=T0, author=PROVIDER_A)
    out = await h.message(
        "out", None, at=T0 + 5 * S, author=PROVIDER_A, reply_to_id=str(entry.key.message_id)
    )
    back = await h.message("back in 2.10 @everyone", None, at=T0 + 20 * S, author=PROVIDER_A)
    for env in (entry, out, back):
        await h.process(env, processor=fast)

    first = await db_session.get(ProviderPosition, opened_position(entry))
    again = await db_session.get(ProviderPosition, opened_position(back))
    assert first is not None and first.status == PositionStatus.CLOSED
    assert again is not None and again.reentry_of_position_id == first.provider_position_id
    assert (again.underlying, again.strike, again.option_right, again.expiry) == (
        first.underlying,
        first.strike,
        first.option_right,
        first.expiry,
    )
    assert again.provider_entry_price == Decimal("2.10")
    [card] = await cards_of(db_session, back)
    assert card.action == CardAction.ENTRY
    assert card.card_type == CardType.INFERRED  # the contract comes from the earlier message


async def test_back_in_with_two_closed_positions_is_a_review(
    h: Harness, db_session: AsyncSession
) -> None:
    fast = h.processor(fixture=False)
    spy = await h.message(SPY_ENTRY, None, at=T0, author=PROVIDER_A)
    qqq = await h.message("QQQ 495p 9/28 .98", None, at=T0 + S, author=PROVIDER_A)
    outs = [
        await h.message(
            "out", None, at=T0 + (5 + i) * S, author=PROVIDER_A, reply_to_id=str(e.key.message_id)
        )
        for i, e in enumerate((spy, qqq))
    ]
    back = await h.message("back in", None, at=T0 + 20 * S, author=PROVIDER_A)
    for env in (spy, qqq, *outs, back):
        await h.process(env, processor=fast)
    [card] = await cards_of(db_session, back)
    assert card.card_type == CardType.REVIEW  # no model configured: a person decides
    assert all(p.reentry_of_position_id is None for p in await positions(db_session))
