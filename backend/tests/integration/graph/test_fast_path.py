"""F070: obvious messages are settled by code without calling Jev; anything else still goes to
Jev. Real PG, scripted Jev client that counts calls."""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.cards.models import Card, CardAction
from app.config import get_settings
from app.db import create_session_factory
from app.graph.processor import GraphProcessor
from app.inbox.models import StageTiming
from app.interpret.jev.client import JevInterpreter
from app.interpret.models import LinkMethod, MessageLink, Signal
from app.ledger.models import ProviderPosition
from app.pipeline.apply import signal_id_for
from app.runtime_config.schema import JevConfig, RuntimeConfig
from tests.integration.conftest import _url
from tests.integration.interpret.test_jev_client import ScriptedJev, response
from tests.integration.pipeline.harness import T0, Factory, Harness, opened_position
from tests.jev_helpers import c, example_answers
from tests.messages import PROVIDER_A

S = timedelta(seconds=1)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path, jev=JevConfig(model="jev-test", enabled=True))


def pipeline(h: Harness, client: ScriptedJev) -> GraphProcessor:
    jev = JevInterpreter(
        h.factory,
        get_settings(),
        lambda: h.config.get() or RuntimeConfig(),
        h.clock,
        client=client,  # type: ignore[arg-type]
    )
    return h.processor(fixture=False, jev=jev)


async def test_entry_and_replied_trim_never_call_jev(h: Harness, db_session: AsyncSession) -> None:
    client = ScriptedJev()  # any call would pop from an empty script and fail
    p = pipeline(h, client)
    entry = await h.message("SPY 580p 9/28 2.30", None, at=T0, author=PROVIDER_A)
    trim = await h.message(
        "trim half",
        None,
        at=T0 + 5 * S,
        author=PROVIDER_A,
        reply_to_id=str(entry.key.message_id),
    )
    for env in (entry, trim):
        await h.process(env, processor=p)

    assert client.calls == 0
    sigs = {s.message_id: s for s in (await db_session.scalars(select(Signal))).all()}
    assert {s.interpreter for s in sigs.values()} == {"fast"}
    position = await db_session.get(ProviderPosition, opened_position(entry))
    assert position is not None
    assert (position.underlying, position.strike, position.remaining_fraction) == (
        "SPY",
        Decimal(580),
        Decimal("0.5"),
    )
    link = await db_session.scalar(
        select(MessageLink).where(MessageLink.signal_id == signal_id_for(trim.key, 0))
    )
    assert link is not None and link.method == LinkMethod.REPLY
    entry_card = await db_session.scalar(
        select(Card).where(Card.signal_id == signal_id_for(entry.key, 0))
    )
    assert entry_card is not None and entry_card.action == CardAction.ENTRY
    stages = (await db_session.scalars(select(StageTiming.stage))).all()
    assert "fast" in stages and "jev" not in stages


async def test_an_unresolved_trim_goes_to_jev(h: Harness, db_session: AsyncSession) -> None:
    client = ScriptedJev(
        response(
            example_answers(
                msg_type=c("trim"),
                exit_fraction=c("half"),
                ticker=c("not_stated"),
                links_to=c("ambiguous", 0.6),
            )
        )
    )
    p = pipeline(h, client)
    spy = await h.message("SPY 580p 9/28 2.30", None, at=T0, author=PROVIDER_A)
    qqq = await h.message("QQQ 495p 9/28 .98", None, at=T0 + S, author=PROVIDER_A)
    trim = await h.message("trim half", None, at=T0 + 5 * S, author=PROVIDER_A)
    for env in (spy, qqq, trim):
        await h.process(env, processor=p)
    assert client.calls == 1  # the two entries were fast, the ambiguous trim asked Jev


async def test_a_conditional_goes_to_jev(h: Harness) -> None:
    client = ScriptedJev(response(example_answers(msg_type=c("conditional"))))
    p = pipeline(h, client)
    env = await h.message("if SPY holds 580 then calls", None, at=T0, author=PROVIDER_A)
    await h.process(env, processor=p)
    assert client.calls == 1
