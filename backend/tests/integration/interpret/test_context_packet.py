"""F073: the context packet a model gets: the lane's own recent messages (same window live and
in evaluation), its book, the resolver's candidates and the lane's recent signal events."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes.labels import _recent_n
from app.db import create_session_factory
from app.interpret.context import load_context
from app.runtime_config.schema import RuntimeConfig
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import T0, Factory, Harness, opened_position
from tests.messages import CHANNEL, PROVIDER_A, PROVIDER_B

S = timedelta(seconds=1)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(
        factory, tmp_path, channels=[CHANNEL, CHANNEL + 1], context_recent_messages=5
    )


async def test_the_packet_is_the_lanes_and_says_what_happened_last(h: Harness) -> None:
    fast = h.processor(fixture=False)
    spy = await h.message("SPY 580p 9/28 2.30", None, at=T0, author=PROVIDER_A)
    trim = await h.message(
        "trim half", None, at=T0 + S, author=PROVIDER_A, reply_to_id=str(spy.key.message_id)
    )
    other_channel = await h.message(
        "QQQ 495p 9/28 .98", None, at=T0 + 2 * S, author=PROVIDER_A, channel=CHANNEL + 1
    )
    other_author = await h.message("TSLA 400c 10/2 3.10", None, at=T0 + 3 * S, author=PROVIDER_B)
    now = await h.message("still holding", None, at=T0 + 10 * S, author=PROVIDER_A)
    for env in (spy, trim, other_channel, other_author):
        await h.process(env, processor=fast)

    async with h.factory() as db:
        ctx = await load_context(
            db, now, recent_n=(h.config.get() or RuntimeConfig()).context_recent_messages, now=T0
        )
    assert [m.text for m in ctx.recent_author_messages] == ["SPY 580p 9/28 2.30", "trim half"]
    assert [b.id for b in ctx.provider_book] == [opened_position(spy)]
    assert [(e.position_id, e.summary) for e in ctx.recent_signal_events] == [
        (opened_position(spy), "trim SPY 580P"),
        (opened_position(spy), "open SPY 580P"),
    ]
    dumped = ctx.model_dump_json()
    assert "QQQ" not in dumped and "TSLA" not in dumped


def test_labeling_reads_the_live_window(h: Harness) -> None:
    assert _recent_n(h.config) == 5
