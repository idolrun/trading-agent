"""F035: every route through the message graph with fake Jev / LLM / market data, real PG."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert
from app.cards.models import Card
from app.config import get_settings
from app.db import create_session_factory
from app.graph.checkpointer import open_pool, saver_and_store
from app.inbox.envelope import Envelope
from app.inbox.models import InboxItem, StageTiming
from app.interpret.jev.client import JevFailed, JevInterpreter, JevOutcome
from app.interpret.laya.client import LayaInterpreter
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.models import Signal
from app.interpret.signal import ParsedSignals
from app.llm.chain import ChainResult, LlmChain
from app.llm.models import ModelCall
from app.main import libpq_dsn
from app.runtime_config.schema import (
    ChainEntry,
    JevConfig,
    LayaConfig,
    LlmConfig,
    LlmRoles,
    RoleConfig,
    RuntimeConfig,
)
from app.system.models import FlagName, SystemFlag
from tests.integration.conftest import _url
from tests.integration.interpret.test_jev_client import ScriptedJev, rate_limited, response
from tests.integration.pipeline.harness import T0, Factory, Harness
from tests.jev_helpers import EXAMPLE, c, example_answers
from tests.laya_helpers import FakeLaya
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


def jev_down(h: Harness) -> JevInterpreter:
    """Rate limited twice: the client gives up and returns no answer."""
    client = ScriptedJev(rate_limited(), rate_limited())
    return JevInterpreter(
        h.factory,
        get_settings(),
        lambda: h.config.get() or RuntimeConfig(),
        h.clock,
        client=client,  # type: ignore[arg-type]
    )


async def laya(h: Harness, fake: FakeLaya) -> LayaInterpreter:
    interpreter = LayaInterpreter(
        h.factory,
        get_settings(),
        lambda: h.config.get() or RuntimeConfig(),
        h.clock,
        h.alerts,
        loader=fake.load,
    )
    await interpreter.warm()
    return interpreter


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


async def test_llm_is_optional_unsure_jev_goes_to_review_without_degrading(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    """No chain configured (the LLM is slow and optional): no call, no parser_degraded flag, and
    the card says why Jev did not settle the message."""
    no_llm = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON)
    env = await example(no_llm)
    unsure = jev(no_llm, msg_type=c("entry", 0.6))
    await no_llm.process(env, processor=no_llm.processor(fixture=False, jev=unsure))
    [card] = await cards(db_session)
    [reason] = card.proposal["review_reasons"]
    assert reason.startswith("needs_review: ") and "t_type" in reason
    flag = await db_session.get(SystemFlag, FlagName.PARSER_DEGRADED)
    assert flag is None or not flag.active
    assert await db_session.scalar(select(func.count()).select_from(ModelCall.__table__)) == 1


async def test_llm_is_optional_jev_unavailable_is_a_review_card(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    no_llm = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON)
    env = await example(no_llm)
    await no_llm.process(env, processor=no_llm.processor(fixture=False))
    [card] = await cards(db_session)
    assert card.proposal["review_reasons"] == ["needs_review: jev_disabled"]
    assert await db_session.scalar(select(func.count()).select_from(ModelCall.__table__)) == 0


async def test_without_an_llm_a_missing_underlying_is_a_confirm_the_underlying_card(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    """FR-14: the card carries the contract Jev read; the user supplies the ticker."""
    no_llm = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON)
    env = await no_llm.message("770c .95 0dte lotto", None, author=PROVIDER_A)
    no_ticker = jev(no_llm, ticker=c("not_stated"), expiry=c("e1", 0.95))
    await no_llm.process(env, processor=no_llm.processor(fixture=False, jev=no_ticker))
    [signal] = await signals(db_session)
    assert (signal.msg_type, signal.interpreter) == ("entry", "jev")
    [card] = await cards(db_session)
    assert "underlying_missing" in card.proposal["review_reasons"]


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


LAYA_ON = LayaConfig(enabled=True)


async def test_laya_answers_when_jev_is_down(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers())
    env = await example(h)
    processor = h.processor(fixture=False, jev=jev_down(h), laya=await laya(h, fake))
    await h.process(env, processor=processor)
    [signal] = await signals(db_session)
    assert (signal.msg_type, signal.interpreter) == ("entry", "laya")
    assert signal.model_call_id is not None
    assert len(await cards(db_session)) == 1
    providers = (await db_session.scalars(select(ModelCall.provider).order_by(ModelCall.id))).all()
    assert providers == ["typesafe", "typesafe", "laya"]
    flag = await db_session.get(SystemFlag, FlagName.PARSER_DEGRADED)
    assert flag is None or not flag.active
    stages = set((await db_session.scalars(select(StageTiming.stage))).all())
    assert "laya" in stages  # F074: timed as itself, not as Jev


async def test_laya_is_not_asked_when_jev_is_unsure(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    """F078: an unsure Jev is a semantic doubt (the LLM's job, else review), not a model
    failure: asking a weaker model for another answer would not make it safer."""
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers())
    env = await example(h)
    unsure = jev(h, msg_type=c("entry", 0.6))
    await h.process(env, processor=h.processor(fixture=False, jev=unsure, laya=await laya(h, fake)))
    assert fake.calls == 0
    [card] = await cards(db_session)
    assert card.card_type == "review"
    assert card.proposal["review_reasons"][0].startswith("needs_review: msg_type: below t_type")
    inbox_row = await db_session.get(InboxItem, (env.key.message_id, "create", 0))
    assert inbox_row is not None and inbox_row.interpret_outcome == "review"
    assert inbox_row.interpret_reasons is not None
    assert inbox_row.interpret_reasons["jev"] == "ambiguous"
    assert inbox_row.interpret_reasons["llm"] == "not_configured"
    assert "laya" not in inbox_row.interpret_reasons


async def test_unsure_jev_with_an_llm_goes_to_the_llm_not_laya(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LLM, jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers())
    env = await example(h)
    unsure = jev(h, msg_type=c("entry", 0.6))
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    processor = h.processor(fixture=False, jev=unsure, laya=await laya(h, fake), llm=extractor)
    await h.process(env, processor=processor)
    assert fake.calls == 0
    [signal] = await signals(db_session)
    assert signal.interpreter == "llm"
    inbox_row = await db_session.get(InboxItem, (env.key.message_id, "create", 0))
    assert inbox_row is not None and inbox_row.interpret_outcome == "fallback_success"


async def test_laya_is_not_asked_when_jev_settles_the_message(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers())
    env = await example(h)
    await h.process(env, processor=h.processor(fixture=False, jev=jev(h), laya=await laya(h, fake)))
    assert fake.calls == 0  # a settled Jev answer is never second-guessed
    [signal] = await signals(db_session)
    assert (signal.msg_type, signal.interpreter) == ("entry", "jev")


async def test_jev_down_and_unsure_laya_end_in_a_review_card_without_orders(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers(msg_type=c("entry", 0.5)))
    env = await example(h)
    await h.process(
        env, processor=h.processor(fixture=False, jev=jev_down(h), laya=await laya(h, fake))
    )
    assert fake.calls == 1
    [card] = await cards(db_session)
    assert card.card_type == "review"
    [reason] = card.proposal["review_reasons"]
    assert reason.startswith("needs_review: jev_rate_limited") and "laya: msg_type" in reason
    inbox_row = await db_session.get(InboxItem, (env.key.message_id, "create", 0))
    assert inbox_row is not None and inbox_row.interpret_reasons is not None
    assert inbox_row.interpret_reasons["jev"] == "model_failure"
    assert inbox_row.interpret_reasons["laya"] == "unsure"


async def test_a_doubtful_laya_answer_continues_to_the_llm(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LLM, jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers(msg_type=c("entry", 0.85)))  # below Laya's 0.9 gate
    env = await example(h)
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    processor = h.processor(fixture=False, jev=jev_down(h), laya=await laya(h, fake), llm=extractor)
    await h.process(env, processor=processor)
    assert fake.calls == 1
    [signal] = await signals(db_session)
    assert signal.interpreter == "llm"


async def test_laya_that_cannot_load_leaves_the_outage_to_the_review_card(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)

    def broken(_model: str) -> Any:
        raise ImportError("No module named 'mlx'")

    dead = LayaInterpreter(
        h.factory,
        get_settings(),
        lambda: h.config.get() or RuntimeConfig(),
        h.clock,
        h.alerts,
        loader=broken,
    )
    await dead.warm()
    env = await example(h)
    await h.process(env, processor=h.processor(fixture=False, jev=jev_down(h), laya=dead))
    [card] = await cards(db_session)
    assert card.proposal["review_reasons"] == ["needs_review: jev_rate_limited"]


# --- F078: why Jev did not settle a message decides where it goes ------------------------------


def jev_slow(h: Harness) -> JevInterpreter:
    """Every request outlives `timeout_ms` (50 ms here): a model failure, never a hang."""
    client = ScriptedJev(response(example_answers()), delay=0.3)
    cfg = lambda: (h.config.get() or RuntimeConfig()).model_copy(  # noqa: E731
        update={"jev": JevConfig(model="jev-test", enabled=True, timeout_ms=50)}
    )
    return JevInterpreter(h.factory, get_settings(), cfg, h.clock, client=client)  # type: ignore[arg-type]


class ScopeBlockedJev(JevInterpreter):
    async def interpret(self, *args: Any, **kwargs: Any) -> JevOutcome:
        return JevFailed("scope_violation", "lot id in model request")


async def outcome_of(db: AsyncSession, env: Envelope) -> InboxItem:
    row = await db.get(InboxItem, (env.key.message_id, env.key.event_type, env.key.event_seq))
    assert row is not None
    await db.refresh(row)
    return row


async def test_a_jev_timeout_goes_to_laya(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON, laya=LAYA_ON)
    fake = FakeLaya(example_answers())
    env = await example(h)
    await h.process(
        env, processor=h.processor(fixture=False, jev=jev_slow(h), laya=await laya(h, fake))
    )
    assert fake.calls == 1
    [signal] = await signals(db_session)
    assert signal.interpreter == "laya"
    row = await outcome_of(db_session, env)
    assert row.interpret_outcome == "fallback_success"
    assert row.interpret_reasons is not None
    assert (row.interpret_reasons["jev"], row.interpret_reasons["jev_detail"]) == (
        "model_failure",
        "timeout",
    )


async def test_an_invalid_jev_answer_never_poisons_the_event(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    """An option Jev was never offered: audited as schema_invalid, then the fallbacks; with
    none configured, a review card on the first attempt."""
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON)
    env = await example(h)
    await h.process(env, processor=h.processor(fixture=False, jev=jev(h, ticker=c("t9"))))
    [card] = await cards(db_session)
    assert card.proposal["review_reasons"] == ["needs_review: jev_invalid"]
    row = await outcome_of(db_session, env)
    assert (row.status, row.last_error, row.interpret_outcome) == ("done", None, "review")
    assert row.interpret_reasons is not None and row.interpret_reasons["jev"] == "invalid"


async def test_jev_down_without_laya_goes_to_the_configured_llm(
    h: Harness, db_session: AsyncSession
) -> None:
    env = await example(h)
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    await h.process(env, processor=h.processor(fixture=False, jev=jev_down(h), llm=extractor))
    [signal] = await signals(db_session)
    assert signal.interpreter == "llm"
    row = await outcome_of(db_session, env)
    assert row.interpret_outcome == "fallback_success"
    assert row.interpret_reasons is not None and row.interpret_reasons["laya"] == "off"


async def test_a_scope_violation_is_a_review_and_an_urgent_alert_with_no_other_model(
    h: Harness, db_session: AsyncSession
) -> None:
    fake = FakeLaya(example_answers())
    blocked = ScopeBlockedJev(h.factory, get_settings(), lambda: RuntimeConfig(), h.clock)
    env = await example(h)
    extractor = llm(h, [structured(SCHEMA, {"signals": [ENTRY]})])
    processor = h.processor(fixture=False, jev=blocked, laya=await laya(h, fake), llm=extractor)
    await h.process(env, processor=processor)
    assert fake.calls == 0  # no other model was asked
    assert [s.interpreter for s in await signals(db_session)] == ["none"]  # the raw review
    [card] = await cards(db_session)
    assert card.proposal["review_reasons"] == ["scope_violation"]
    [alert] = (await db_session.scalars(select(Alert))).all()
    assert (alert.kind, alert.severity) == ("invariant_block", "urgent")
    row = await outcome_of(db_session, env)
    assert row.interpret_outcome == "scope_violation"
    assert await db_session.scalar(select(func.count()).select_from(ModelCall.__table__)) == 0


async def test_settled_messages_record_their_outcome(h: Harness, db_session: AsyncSession) -> None:
    env = await example(h)
    await h.process(env, processor=h.processor(fixture=False, jev=jev(h)))
    row = await outcome_of(db_session, env)
    assert row.interpret_outcome == "jev_success"
    assert row.interpret_reasons == {"jev": "success", "route": "assemble"}


async def test_confident_chatter_with_a_position_open_is_a_review_not_feed_only(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    """F079: "gotta go here" read as chatter at 0.95 while the provider holds SPY: a person
    sees it (no LLM configured); without a position the same answer stays feed-only."""
    h = await Harness.create(factory, tmp_path, llm=LlmConfig(), jev=JEV_ON)
    entry = await h.message("SPY 9/28 580P 2.32", None, at=T0, author=PROVIDER_A)
    await h.process(entry, processor=h.processor(fixture=False))
    chatter = {"msg_type": c("chatter", 0.95), "ticker": c("not_stated")}
    leave = await h.message("gotta go here", None, at=T0 + timedelta(seconds=5), author=PROVIDER_A)
    jev_says_chatter = jev(h, **chatter)
    await h.process(leave, processor=h.processor(fixture=False, jev=jev_says_chatter))
    review = (await db_session.scalars(select(Card).where(Card.card_type == "review"))).one()
    [reason] = review.proposal["review_reasons"]
    assert reason.startswith("needs_review: feed_guard: chatter but leaving 'gotta go here'")
    row = await outcome_of(db_session, leave)
    assert (row.interpret_outcome, row.interpret_reasons["jev"]) == ("review", "ambiguous")  # type: ignore[index]

    other = await h.message("gotta go here", None, at=T0 + timedelta(seconds=9), author=PROVIDER_B)
    await h.process(other, processor=h.processor(fixture=False, jev=jev(h, **chatter)))
    assert (await outcome_of(db_session, other)).interpret_outcome == "jev_success"  # feed only
