"""Laya client: loads off the hot path, answers under the strict gate, audits every call, and a
timeout never leaves two calls on the model."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.config import get_settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.laya.client import LayaInterpreter
from app.llm.models import CallOutcome, ModelCall
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import LayaConfig
from tests.integration.conftest import _url
from tests.jev_helpers import CFG, EXAMPLE, c, example_answers
from tests.laya_helpers import FakeLaya
from tests.llm_helpers import ctx

REF = CallRef()
ON = CFG.model_copy(update={"laya": LayaConfig(enabled=True, timeout_ms=2000)})


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def laya_for(
    sessions: async_sessionmaker[AsyncSession], fake: FakeLaya | Callable[[str], Any], cfg: Any = ON
) -> LayaInterpreter:
    loader = fake.load if isinstance(fake, FakeLaya) else fake
    return LayaInterpreter(
        sessions,
        get_settings(),
        lambda: cfg,
        FixedClock(datetime(2026, 9, 28, 14, 30, tzinfo=UTC)),
        AlertService(sessions),
        loader=loader,
    )


async def interpret(laya: LayaInterpreter) -> Any:
    return await laya.interpret(ctx(EXAMPLE), find_candidates(EXAMPLE), ref=REF)


async def test_answers_after_loading_and_audits_the_call(
    sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    fake = FakeLaya(example_answers())
    laya = laya_for(sessions, fake)
    await laya.warm()
    assert laya.state == "ready" and fake.loads == [get_settings().laya_model]
    result = await interpret(laya)
    assert result.parsed is not None and result.parsed.interpreter == "laya"
    [call] = (await db_session.scalars(select(ModelCall))).all()
    assert (call.provider, call.role, call.outcome) == ("laya", "interpret", CallOutcome.OK)
    assert call.tokens_in == 1200 and call.id == result.model_call_id


async def test_disabled_never_loads_or_answers(sessions: async_sessionmaker[AsyncSession]) -> None:
    fake = FakeLaya(example_answers())
    laya = laya_for(sessions, fake, CFG)  # laya.enabled defaults to False
    assert await interpret(laya) is None
    assert fake.loads == [] and laya.state == "idle"


async def test_first_use_starts_loading_and_answers_none_until_ready(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeLaya(example_answers())
    laya = laya_for(sessions, fake)
    assert await interpret(laya) is None  # not loaded yet: the caller moves on
    assert laya._warming is not None
    await laya._warming
    assert await interpret(laya) is not None


async def test_load_failure_raises_one_alert_and_stays_off(
    sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    def broken(_model: str) -> Any:
        raise ImportError("No module named 'mlx'")

    laya = laya_for(sessions, broken)
    await laya.warm()
    await laya.warm()
    assert laya.state == "failed" and await interpret(laya) is None
    alerts = (await db_session.scalars(select(Alert))).all()
    assert [(a.kind, a.severity) for a in alerts] == [("provider_disabled", "warning")]


async def test_a_timeout_is_audited_and_the_model_slot_stays_busy_until_it_finishes(
    sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    fake = FakeLaya(example_answers(), delay_s=0.4)
    cfg = CFG.model_copy(update={"laya": LayaConfig(enabled=True, timeout_ms=100)})
    laya = laya_for(sessions, fake, cfg)
    await laya.warm()
    assert await interpret(laya) is None
    assert await interpret(laya) is None  # the first call still holds the only slot: busy
    assert fake.calls == 1  # the second never reached the model
    await asyncio.sleep(0.5)  # the abandoned call finishes and frees the slot
    assert await interpret(laya) is None  # (100 ms timeout again, but it did reach the model)
    assert fake.calls == 2
    outcomes = (await db_session.scalars(select(ModelCall.outcome).order_by(ModelCall.id))).all()
    assert outcomes == [CallOutcome.TIMEOUT, CallOutcome.BUSY, CallOutcome.TIMEOUT]


async def test_a_second_lane_never_waits_for_the_model(
    sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    """F074: two lanes at once: one gets the model, the other is skipped at once (busy), so a
    slow local call never stretches another lane's queue."""
    fake = FakeLaya(example_answers(), delay_s=0.3)
    laya = laya_for(sessions, fake)
    await laya.warm()
    first = asyncio.create_task(interpret(laya))
    await asyncio.sleep(0.05)  # the first call holds the slot
    t0 = asyncio.get_running_loop().time()
    assert await interpret(laya) is None
    assert asyncio.get_running_loop().time() - t0 < 0.1  # not 0.3 s behind the first
    assert (await first) is not None
    outcomes = sorted((await db_session.scalars(select(ModelCall.outcome))).all())
    assert outcomes == sorted([CallOutcome.OK, CallOutcome.BUSY])


async def test_a_book_too_large_for_its_window_is_skipped(
    sessions: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    import uuid

    fake = FakeLaya(example_answers())
    laya = laya_for(sessions, fake)
    await laya.warm()
    big = ctx(EXAMPLE, book=[(uuid.uuid4(), f"SPY {i}C") for i in range(6)])
    assert await laya.interpret(big, find_candidates(EXAMPLE), ref=REF) is None
    assert fake.calls == 0
    assert (await db_session.scalars(select(ModelCall))).all() == []


async def test_a_large_book_narrowed_by_the_resolver_still_fits(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """F078: the window limit counts the resolver's candidates, not the whole lane book."""
    import uuid

    fake = FakeLaya(example_answers())
    laya = laya_for(sessions, fake)
    await laya.warm()
    big = ctx(EXAMPLE, book=[(uuid.uuid4(), f"SPY {i}C") for i in range(6)])
    narrowed = big.model_copy(update={"link_candidates": big.provider_book[:1]})
    assert await laya.interpret(narrowed, find_candidates(EXAMPLE), ref=REF) is not None
    assert fake.calls == 1


async def test_strict_gate_leaves_a_doubtful_answer_to_the_router(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    from app.interpret.laya.client import gate
    from app.interpret.route import route

    fake = FakeLaya(
        example_answers(msg_type=c("entry", 0.85))
    )  # fine for Jev's 0.8, not Laya's 0.9
    laya = laya_for(sessions, fake)
    await laya.warm()
    result = await interpret(laya)
    assert route(result, gate(ON)).path == "slow_path"
