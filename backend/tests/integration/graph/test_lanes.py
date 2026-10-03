"""F068: the lane (channel, provider) is the context boundary: books, links and same-contract
adds never cross a channel or an author, whatever the interpreter suggests."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory
from app.inbox.envelope import Envelope
from app.interpret.models import LinkOutcome, MessageLink
from app.interpret.signal import ParsedSignal
from app.ledger.models import ProviderPosition
from app.pipeline.apply import signal_id_for
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    opened_position,
    option_entry,
)
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B

S = timedelta(seconds=1)
CHANNEL_B = CHANNEL + 1


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path, channels=[CHANNEL, CHANNEL_B])


def spy() -> ParsedSignal:
    return option_entry("SPY", "580", "580p", "9/28", "2.30")


def qqq() -> ParsedSignal:
    return option_entry("QQQ", "495", "495p", "9/28", ".98")


async def link_of(db: AsyncSession, env: Envelope) -> MessageLink:
    link = await db.scalar(
        select(MessageLink).where(MessageLink.signal_id == signal_id_for(env.key, 0))
    )
    assert link is not None
    return link


async def test_follow_up_resolves_inside_its_channel(h: Harness, db_session: AsyncSession) -> None:
    spy_a = await h.message("SPY 580p 9/28 2.30", [spy()], at=T0, author=PROVIDER_A)
    qqq_b = await h.message(
        "QQQ 495p 9/28 .98",
        [qqq()],
        at=T0 + S,
        author=PROVIDER_A,
        channel=CHANNEL_B,
    )
    p_spy, p_qqq = opened_position(spy_a), opened_position(qqq_b)
    trim_b = await h.message(
        "trim half",
        [follow_up("trim", p_qqq, exit_fraction="half")],
        at=T0 + 2 * S,
        author=PROVIDER_A,
        channel=CHANNEL_B,
    )
    for env in (spy_a, qqq_b, trim_b):
        await h.process(env)

    assert (await link_of(db_session, trim_b)).provider_position_id == p_qqq
    spy_row = await db_session.get(ProviderPosition, p_spy)
    qqq_row = await db_session.get(ProviderPosition, p_qqq)
    assert spy_row is not None and qqq_row is not None
    assert (spy_row.opened_channel_id, spy_row.remaining_fraction) == (CHANNEL, 1)
    assert (qqq_row.opened_channel_id, qqq_row.remaining_fraction) == (CHANNEL_B, Decimal("0.5"))


async def test_a_hint_at_another_channels_position_is_refused(
    h: Harness, db_session: AsyncSession
) -> None:
    """The interpreter names the SPY position of channel A for a channel-B trim: the proposal is
    refused and the trim goes to channel B's sole position by code (F077)."""
    spy_a = await h.message("SPY 580p 9/28 2.30", [spy()], at=T0, author=PROVIDER_A)
    qqq_b = await h.message(
        "QQQ 495p 9/28 .98",
        [qqq()],
        at=T0 + S,
        author=PROVIDER_A,
        channel=CHANNEL_B,
    )
    p_spy, p_qqq = opened_position(spy_a), opened_position(qqq_b)
    trim_b = await h.message(
        "trim half",
        [follow_up("trim", p_spy, conf="0.99", exit_fraction="half")],
        at=T0 + 2 * S,
        author=PROVIDER_A,
        channel=CHANNEL_B,
    )
    for env in (spy_a, qqq_b, trim_b):
        await h.process(env)

    link = await link_of(db_session, trim_b)
    assert (link.outcome, link.provider_position_id) == (LinkOutcome.LINKED, p_qqq)
    assert link.evidence is not None and link.evidence["resolver"]["rule"] == "sole_candidate"
    assert link.evidence["candidates"] == [str(p_qqq)]  # the lane's book only
    assert link.evidence["rule"] == "code"
    spy_row = await db_session.get(ProviderPosition, p_spy)
    assert spy_row is not None and spy_row.remaining_fraction == 1


async def test_same_channel_other_author_is_never_a_target(
    h: Harness, db_session: AsyncSession
) -> None:
    spy_x = await h.message("SPY 580p 9/28 2.30", [spy()], at=T0, author=PROVIDER_A)
    p_spy = opened_position(spy_x)
    trim_y = await h.message(
        "trim half",
        [follow_up("trim", p_spy, conf="0.99", exit_fraction="half")],
        at=T0 + S,
        author=PROVIDER_B,
    )
    for env in (spy_x, trim_y):
        await h.process(env)

    link = await link_of(db_session, trim_y)
    assert (link.outcome, link.provider_position_id) == (LinkOutcome.NONE, None)
    spy_row = await db_session.get(ProviderPosition, p_spy)
    assert spy_row is not None and spy_row.remaining_fraction == 1


async def test_same_contract_in_two_channels_is_two_positions(
    h: Harness, db_session: AsyncSession
) -> None:
    """C62 (an entry on an open contract is an add) holds inside a lane only."""
    a = await h.message("SPY 580p 9/28 2.30", [spy()], at=T0, author=PROVIDER_A)
    b = await h.message(
        "SPY 580p 9/28 2.30",
        [spy()],
        at=T0 + S,
        author=PROVIDER_A,
        channel=CHANNEL_B,
    )
    again = await h.message("SPY 580p 9/28 2.30", [spy()], at=T0 + 2 * S, author=PROVIDER_A)
    for env in (a, b, again):
        await h.process(env)

    rows = (await db_session.scalars(select(ProviderPosition))).all()
    assert sorted(r.opened_channel_id for r in rows) == [CHANNEL, CHANNEL_B]
    assert (await link_of(db_session, b)).outcome == LinkOutcome.NEW_POSITION
    again_link = await link_of(db_session, again)
    assert (again_link.outcome, again_link.provider_position_id) == (
        LinkOutcome.LINKED,
        opened_position(a),
    )
