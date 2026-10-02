"""F069: the context resolver in the message graph. Reply chains, threads and a stated contract
decide the target before any model; a model pick never overrides them."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.cards.models import Card, CardType
from app.db import create_session_factory
from app.inbox.envelope import Envelope
from app.interpret.models import LinkMethod, LinkOutcome, MessageLink
from app.interpret.signal import ParsedSignal, SignalFields
from app.ledger.models import ProviderPosition
from app.pipeline.apply import signal_id_for
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    lit,
    opened_position,
    option_entry,
)
from tests.messages import PROVIDER_A

S = timedelta(seconds=1)
THREAD = 990000000000000077


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path)


async def two_positions(h: Harness, **spy_extra: str) -> tuple[Envelope, Envelope]:
    spy = await h.message(
        "SPY 580p 9/28 2.30",
        [option_entry("SPY", "580", "580p", "9/28", "2.30")],
        at=T0,
        author=PROVIDER_A,
        **spy_extra,
    )
    qqq = await h.message(
        "QQQ 495p 9/28 .98",
        [option_entry("QQQ", "495", "495p", "9/28", ".98")],
        at=T0 + S,
        author=PROVIDER_A,
    )
    for env in (spy, qqq):
        await h.process(env)
    return spy, qqq


async def link_of(db: AsyncSession, env: Envelope) -> MessageLink:
    link = await db.scalar(
        select(MessageLink).where(MessageLink.signal_id == signal_id_for(env.key, 0))
    )
    assert link is not None
    return link


async def remaining(db: AsyncSession, env: Envelope) -> Decimal:
    row = await db.get(ProviderPosition, opened_position(env))
    assert row is not None
    await db.refresh(row)
    return row.remaining_fraction


async def test_trim_half_with_two_positions_is_a_review_of_exactly_those_two(
    h: Harness, db_session: AsyncSession
) -> None:
    spy, qqq = await two_positions(h)
    trim = await h.message(
        "trim half",
        [follow_up("trim", "ambiguous", conf="0.5", exit_fraction="half")],
        at=T0 + 10 * S,
        author=PROVIDER_A,
    )
    await h.process(trim)
    link = await link_of(db_session, trim)
    assert link.outcome == LinkOutcome.AMBIGUOUS
    assert link.candidate_position_ids == [opened_position(spy), opened_position(qqq)]
    card = await db_session.scalar(select(Card).where(Card.signal_id == signal_id_for(trim.key, 0)))
    assert card is not None and card.card_type == CardType.REVIEW
    assert await remaining(db_session, spy) == 1 and await remaining(db_session, qqq) == 1


async def test_stated_ticker_beats_a_wrong_model_pick(h: Harness, db_session: AsyncSession) -> None:
    """The message says SPY; the interpreter picks QQQ at 0.99. Code links SPY."""
    spy, qqq = await two_positions(h)
    trim = await h.message(
        "trim SPY half",
        [
            follow_up(
                "trim",
                opened_position(qqq),
                conf="0.99",
                exit_fraction="half",
                underlying=lit("SPY"),
            )
        ],
        at=T0 + 10 * S,
        author=PROVIDER_A,
    )
    await h.process(trim)
    link = await link_of(db_session, trim)
    assert (link.outcome, link.method, link.provider_position_id) == (
        LinkOutcome.LINKED,
        LinkMethod.CONTRACT,
        opened_position(spy),
    )
    assert link.evidence is not None and link.evidence["resolver"]["tickers"] == ["SPY"]
    assert await remaining(db_session, spy) == Decimal("0.5")
    assert await remaining(db_session, qqq) == 1


async def test_reply_chain_entry_comment_trim(h: Harness, db_session: AsyncSession) -> None:
    spy, qqq = await two_positions(h)
    comment = await h.message(
        "looking good",
        [ParsedSignal(msg_type="commentary_pnl", fields=SignalFields())],
        at=T0 + 5 * S,
        author=PROVIDER_A,
        reply_to_id=str(spy.key.message_id),
    )
    trim = await h.message(
        "trim half",
        [follow_up("trim", "ambiguous", conf="0.4", exit_fraction="half")],
        at=T0 + 10 * S,
        author=PROVIDER_A,
        reply_to_id=str(comment.key.message_id),
    )
    for env in (comment, trim):
        await h.process(env)
    link = await link_of(db_session, trim)
    assert (link.outcome, link.method, link.provider_position_id) == (
        LinkOutcome.LINKED,
        LinkMethod.REPLY,
        opened_position(spy),
    )
    assert link.evidence is not None and link.evidence["resolver"]["depth"] == 2
    assert await remaining(db_session, qqq) == 1


async def test_thread_gives_the_target(h: Harness, db_session: AsyncSession) -> None:
    spy, qqq = await two_positions(h, thread_id=str(THREAD))
    trim = await h.message(
        "trim half",
        [follow_up("trim", "ambiguous", conf="0.4", exit_fraction="half")],
        at=T0 + 10 * S,
        author=PROVIDER_A,
        thread_id=str(THREAD),
    )
    await h.process(trim)
    link = await link_of(db_session, trim)
    assert (link.method, link.provider_position_id) == (LinkMethod.THREAD, opened_position(spy))
    assert await remaining(db_session, qqq) == 1
