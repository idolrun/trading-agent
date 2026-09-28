"""F035: every route through the message graph with fake Jev / LLM / market data, real PG."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.cards.models import Card
from app.config import get_settings
from app.db import create_session_factory
from app.graph.checkpointer import open_pool, saver_and_store
from app.inbox.envelope import Envelope
from app.inbox.models import InboxItem
from app.interpret.jev.client import JevInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.models import Signal
from app.interpret.signal import ParsedSignals
from app.llm.chain import ChainResult, LlmChain
from app.llm.models import ModelCall
from app.main import libpq_dsn
from app.runtime_config.schema import (
    ChainEntry,
    JevConfig,
    LlmConfig,
    LlmRoles,
    RoleConfig,
    RuntimeConfig,
)
from app.system.models import FlagName, SystemFlag
from tests.integration.conftest import _url
from tests.integration.interpret.test_jev_client import ScriptedJev, response
from tests.integration.pipeline.harness import T0, Factory, Harness
from tests.jev_helpers import EXAMPLE, c, example_answers
from tests.llm_helpers import FakeChat, FakeRegistry, structured
from tests.messages import PROVIDER_A, PROVIDER_B

SCHEMA = "LlmParsedSignals"
ENTRY = {
    "msg_type": "entry",
    "instrument": "option",
    "underlying": {"raw": "spy"},
    "strike": {"raw": "770c"},
    "right": {"raw": "770c"},
    "entry_price": {"raw": ".95"},
    "size_tier": {"raw": "lotto"},
    "links_to": "new_position",
}
LLM = LlmConfig(
    roles=LlmRoles(
        extract=RoleConfig(
            deadline_ms=5000,
            chain=[
                ChainEntry(provider="deepseek", model="ds-test", prompt="extract@v1"),
                ChainEntry(provider="openai_compat", model="oss-test", prompt="extract@v1"),
            ],
        )
    )
)
JEV_ON = JevConfig(model="jev-test", enabled=True)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path, llm=LLM, jev=JEV_ON)


def llm(h: Harness, first: list[AIMessage], second: list[AIMessage] = ()) -> LlmInterpreter:  # type: ignore[assignment]
    keyed = get_settings().model_copy(
        update={"deepseek_api_key": SecretStr("k"), "openai_compat_api_key": SecretStr("k")}
    )
    chain = LlmChain(
        h.factory,
        keyed,
        lambda: (h.config.get() or RuntimeConfig()).llm,
        h.clock,
        models=FakeRegistry(
            {
                "deepseek": FakeChat(responses=first),
                "openai_compat": FakeChat(responses=list(second)),
            }
        ),
    )
    return LlmInterpreter(chain, h.md, h.alerts)


def jev(h: Harness, **answers: Any) -> JevInterpreter:
    client = ScriptedJev(response(example_answers(**answers)))
    return JevInterpreter(
        h.factory,
        get_settings(),
        lambda: h.config.get() or RuntimeConfig(),
        h.clock,
        client=client,  # type: ignore[arg-type]
    )


NO_ANSWER = AIMessage("I am not sure")  # no structured response -> `empty` -> fallback


async def example(h: Harness, author: int = PROVIDER_A) -> Envelope:
    return await h.message(EXAMPLE, None, author=author)


async def signals(db: AsyncSession) -> list[Signal]:
    return list((await db.scalars(select(Signal))).all())


async def cards(db: AsyncSession) -> list[Card]:
    return list((await db.scalars(select(Card))).all())


async def test_feed_only_chatter(h: Harness, db_session: AsyncSession) -> None:
    env = await example(h)
    await h.process(
        env, processor=h.processor(fixture=False, jev=jev(h, msg_type=c("chatter", 0.97)))
    )
    [signal] = await signals(db_session)
    assert (signal.msg_type, signal.interpreter) == ("chatter", "jev")
    assert signal.model_call_id is not None
    assert await cards(db_session) == []
    assert (await db_session.scalars(select(InboxItem.status))).one() == "done"


async def test_assemble_fast_path(h: Harness, db_session: AsyncSession) -> None:
    env = await example(h)
    await h.process(env, processor=h.processor(fixture=False, jev=jev(h)))
    [signal] = await signals(db_session)
    assert (signal.msg_type, signal.interpreter) == ("entry", "jev")
    [card] = await cards(db_session)
    # the TRD §4.2 example states no expiry: never assumed -> review (FR-15)
    assert card.card_type == "review" and "expiry" in card.proposal["missing_fields"]
    llm_calls = await db_session.scalar(
        select(func.count()).select_from(ModelCall).where(col(ModelCall.role) == "extract")
    )
    assert llm_calls == 0  # the fast path never called an LLM


async def test_slow_path_when_jev_is_off(h: Harness, db_session: AsyncSession) -> None:
    env = await example(h)
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    await h.process(env, processor=h.processor(fixture=False, llm=extractor))
    [signal] = await signals(db_session)
    assert signal.interpreter == "llm" and signal.model_call_id is not None
    assert len(await cards(db_session)) == 1


async def test_exhausted_is_raw_review_and_degraded_until_next_success(
    h: Harness, db_session: AsyncSession
) -> None:
    first = await example(h)
    await h.process(
        first, processor=h.processor(fixture=False, llm=llm(h, [NO_ANSWER], [NO_ANSWER]))
    )
    [card] = await cards(db_session)
    assert card.proposal["review_reasons"] == ["parser_degraded"]
    flag = await db_session.get(SystemFlag, FlagName.PARSER_DEGRADED)
    assert flag is not None and flag.active

    second = await h.message(EXAMPLE, None, at=T0.replace(second=9), author=PROVIDER_A)
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    await h.process(second, processor=h.processor(fixture=False, llm=extractor))
    await db_session.refresh(flag)
    assert not flag.active  # cleared by the next successful LLM answer (AC-9)


async def test_content_rejected_is_raw_review_without_fallback(
    h: Harness, db_session: AsyncSession
) -> None:
    env = await example(h)
    invented = {**ENTRY, "underlying": {"raw": "qqq"}}  # not in the message (AC-11)
    extractor = llm(h, [structured(SCHEMA, {"signals": [invented]})], [NO_ANSWER])
    await h.process(env, processor=h.processor(fixture=False, llm=extractor))
    [card] = await cards(db_session)
    assert card.action == "review" and card.proposal["review_reasons"][0].startswith(
        "llm_content_rejected"
    )
    providers = (await db_session.scalars(select(ModelCall.provider))).all()
    assert providers == ["deepseek"]  # no answer shopping


async def test_contexts_stay_per_provider(h: Harness, db_session: AsyncSession) -> None:
    """AC-18: B's model request never contains A's messages."""
    a1 = await h.message("Bought spy 770c .95 lotto, sl 769 spy", None, author=PROVIDER_A)
    b1 = await h.message(EXAMPLE, None, at=T0.replace(second=3), author=PROVIDER_B)
    answer = structured(SCHEMA, {"signals": [ENTRY]})
    for env in (a1, b1):
        await h.process(env, processor=h.processor(fixture=False, llm=llm(h, [answer])))
    requests = (await db_session.scalars(select(ModelCall.request).order_by(ModelCall.id))).all()
    b_request = str(requests[-1])
    assert str(PROVIDER_A) not in b_request and "Bought spy" not in b_request


async def test_notes_spawned_for_pending_cards(h: Harness, db_session: AsyncSession) -> None:
    written: list[uuid.UUID] = []

    class Notes:
        async def write(self, card_id: uuid.UUID) -> bool:
            written.append(card_id)
            return True

    env = await example(h)
    processor = h.processor(fixture=False, jev=jev(h), notes=Notes())  # type: ignore[arg-type]
    await h.process(env, processor=processor)
    await asyncio.gather(*processor.deps.background)
    [card] = await cards(db_session)
    assert written == [card.card_id]


async def test_crash_mid_llm_then_rerun_makes_one_card(
    h: Harness, db_session: AsyncSession
) -> None:
    """Kill -9 simulation: the first attempt dies inside llm_extract (nothing applied yet);
    the dispatcher's re-run (attempt 2, fresh thread) produces exactly one card."""
    env = await example(h)
    started = asyncio.Event()

    class Hanging(LlmInterpreter):
        async def extract(self, *args: Any, **kwargs: Any) -> ChainResult[ParsedSignals]:
            started.set()
            await asyncio.sleep(30)
            raise AssertionError("not reached")

    hanging = Hanging(llm(h, [])._chain, h.md, h.alerts)
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    await pool.open()
    try:
        saver, _ = saver_and_store(pool)
        crash = asyncio.create_task(
            h.process(env, processor=h.processor(fixture=False, llm=hanging, checkpointer=saver))
        )
        await started.wait()
        crash.cancel()
        with pytest.raises(asyncio.CancelledError):
            await crash
        assert await cards(db_session) == []
        extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
        rerun = h.processor(fixture=False, llm=extractor, checkpointer=saver)
        await h.process(env, attempt=2, processor=rerun)
        await h.process(env, attempt=2, processor=rerun)  # even a duplicate re-run
    finally:
        await pool.close()
    assert len(await cards(db_session)) == 1
    threads = (
        await db_session.scalars(
            text("SELECT thread_id FROM langgraph.checkpoints ORDER BY checkpoint_id")
        )
    ).all()
    base = f"msg:{env.key.message_id}:create:0"
    # durability "exit": one checkpoint per run, also for the killed one (where it stopped)
    assert threads == [f"{base}:1", f"{base}:2", f"{base}:2"]
