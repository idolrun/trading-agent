"""F076 end-to-end: an interleaved stream from two channels and three authors, through the real
message graph (fast path for every message: each follow-up acts on its lane's sole position, F077).
No context crosses a channel, an author or a signal.

    Channel A                          Channel B
    X: SPY 580P 2.32 (entry)
    Y: META 710C 5.30 (entry)
                                       X: QQQ 495P .99 (entry)
    X: trim half
                                       Z: TSLA 400C 3.10 (entry)
    Y: stop breakeven
    X: out runners

(Tickers, strikes and prices fit the market fixtures, so validation passes; Z's NVDA is TSLA.)
"""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import create_session_factory
from app.inbox.models import MessageEvent
from app.interpret.jev.client import JevInterpreter
from app.interpret.models import MessageLink, Signal
from app.ledger.models import (
    PositionChange,
    PositionStatus,
    ProviderPosition,
    ProviderPositionEvent,
)
from app.runtime_config.schema import JevConfig, RuntimeConfig
from tests.integration.conftest import _url
from tests.integration.interpret.test_jev_client import ScriptedJev, response
from tests.integration.pipeline.harness import T0, Factory, Harness, opened_position
from tests.jev_helpers import c, example_answers, n
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B

S = timedelta(seconds=1)
A, B = CHANNEL, CHANNEL + 1
X, Y, Z = PROVIDER_A, PROVIDER_B, PROVIDER_B + 1


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(
        factory, tmp_path, channels=[A, B], jev=JevConfig(model="jev-test", enabled=True)
    )


def follow_up(msg_type: str, target: uuid.UUID, **over: Any) -> Any:
    """What Jev answers for a short follow-up: the type, and which candidate position."""
    return response(
        example_answers(
            msg_type=c(msg_type),
            ticker=c("not_stated"),
            right=c("not_stated"),
            size_tier=c("not_stated"),
            links_to=c(str(target), 0.9),
            **over,
        )
    )


async def test_interleaved_channels_and_authors_never_mix(
    h: Harness, db_session: AsyncSession
) -> None:
    stream = [
        ("SPY 9/28 580P 2.32", X, A),
        ("META 10/2 710C 5.30", Y, A),
        ("QQQ 9/28 495P .99", X, B),
        ("trim half", X, A),
        ("TSLA 10/2 400C 3.10 @everyone", Z, B),
        ("stop breakeven", Y, A),
        ("out runners", X, A),
    ]
    envs = [
        await h.message(text, None, at=T0 + i * S, author=author, channel=channel)
        for i, (text, author, channel) in enumerate(stream)
    ]
    spy, meta, qqq, _trim, tsla, _stop, _out = envs
    p = {
        name: opened_position(e)
        for name, e in zip(("spy", "meta", "qqq", "tsla"), (spy, meta, qqq, tsla), strict=True)
    }
    jev = ScriptedJev(
        follow_up("trim", p["spy"], exit_fraction=c("half")),
        follow_up("stop_move", p["meta"], stop_basis=c("premium", 0.9), breakeven_stop=n(0.95)),
        follow_up("full_exit", p["spy"], exit_fraction=c("all")),
    )
    processor = h.processor(
        fixture=False,
        jev=JevInterpreter(
            h.factory,
            get_settings(),
            lambda: h.config.get() or RuntimeConfig(),
            h.clock,
            client=jev,  # type: ignore[arg-type]
        ),
    )
    for env in envs:  # one at a time, in the interleaved order they were posted
        await h.process(env, processor=processor)

    # F077: every follow-up acts on its lane's sole position, so code settles all seven
    assert jev.calls == 0
    interpreters = {
        s.message_id: s.interpreter for s in (await db_session.scalars(select(Signal))).all()
    }
    assert [interpreters[e.key.message_id] for e in envs] == [
        "fast",
    ] * 7

    async def history(position_id: uuid.UUID) -> list[str]:
        rows = await db_session.scalars(
            select(ProviderPositionEvent.change)
            .where(ProviderPositionEvent.provider_position_id == position_id)
            .order_by(ProviderPositionEvent.id)
        )
        return list(rows.all())

    assert await history(p["spy"]) == [
        PositionChange.OPEN,
        PositionChange.TRIM,
        PositionChange.EXIT,
    ]
    assert await history(p["meta"]) == [PositionChange.OPEN, PositionChange.STOP_MOVE]
    assert await history(p["qqq"]) == [PositionChange.OPEN]
    assert await history(p["tsla"]) == [PositionChange.OPEN]

    rows = {
        r.provider_position_id: r
        for r in (await db_session.scalars(select(ProviderPosition))).all()
    }
    assert set(rows) == set(p.values())  # four signals, no stray positions
    assert rows[p["spy"]].status == PositionStatus.CLOSED
    assert rows[p["meta"]].stop_level == rows[p["meta"]].provider_entry_price  # breakeven
    assert (rows[p["qqq"]].provider_id, rows[p["qqq"]].opened_channel_id) == (X, B)
    assert (rows[p["tsla"]].provider_id, rows[p["tsla"]].opened_channel_id) == (Z, B)

    # every link stays inside its message's lane
    links = (
        await db_session.execute(
            select(MessageLink, Signal, MessageEvent, ProviderPosition)
            .join(Signal, Signal.signal_id == MessageLink.signal_id)
            .join(
                MessageEvent,
                (MessageEvent.message_id == Signal.message_id)
                & (MessageEvent.event_type == Signal.event_type),
            )
            .join(
                ProviderPosition,
                ProviderPosition.provider_position_id == MessageLink.provider_position_id,
            )
        )
    ).all()
    assert len(links) == 7
    for _link, sig, event, position in links:
        assert (position.provider_id, position.opened_channel_id) == (
            event.provider_id,
            event.channel_id,
        ), f"message {sig.message_id} linked outside its lane"
