"""F033/F078 client: retry once on 429/529 within the budget, audit every attempt with
probabilities, and say why there is no answer (`JevFailed.kind`) instead of raising."""

import asyncio
import math
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col
from typesafe_sdk import AsyncTypeSafeClient, SystemOneResponse, TypeSafeRateLimitError, Usage

from app.config import Settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.jev.client import JevAnswered, JevFailed, JevInterpreter
from app.llm.models import CallOutcome, ModelCall
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import JevConfig
from tests.integration.conftest import _url
from tests.jev_helpers import CFG, EXAMPLE, c, example_answers, n
from tests.llm_helpers import ctx

JEV_ON = CFG.model_copy(update={"jev": JevConfig(model="jev-test", enabled=True)})


class ScriptedJev:
    """Stands in for `AsyncTypeSafeClient`: each call pops the next exception or response.
    `delay` (s) makes every call that slow."""

    def __init__(self, *script: BaseException | SystemOneResponse, delay: float = 0) -> None:
        self.script = list(script)
        self.calls = 0
        self.delay = delay

    async def system_one(self, **kwargs: Any) -> SystemOneResponse:
        self.calls += 1
        step = self.script.pop(0)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(step, BaseException):
            raise step
        return step


def rate_limited() -> TypeSafeRateLimitError:
    return TypeSafeRateLimitError(429, {"error": "slow down"}, httpx2.Headers())


def response(answers: Mapping[str, Any]) -> SystemOneResponse:
    return SystemOneResponse.model_construct(
        model="jev-test", usage=Usage(input_tokens=900, output_tokens=40), answers=dict(answers)
    )


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def jev(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    fake: ScriptedJev,
    **budget: int,
) -> JevInterpreter:
    keyed = settings.model_copy(update={"typesafe_api_key": SecretStr("k")})
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    client: AsyncTypeSafeClient = fake  # type: ignore[assignment]
    cfg = JEV_ON.model_copy(
        update={"jev": JevConfig(model="jev-test", enabled=True, **budget)}  # type: ignore[arg-type]
    )
    return JevInterpreter(sessions, keyed, lambda: cfg, clock, client=client)


async def rows(db: AsyncSession) -> list[ModelCall]:
    return list(await db.scalars(select(ModelCall).order_by(col(ModelCall.id))))


async def test_rate_limited_twice_degrades_to_the_slow_path(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    fake = ScriptedJev(rate_limited(), rate_limited())
    result = await jev(sessions, settings, fake).interpret(
        ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef()
    )
    assert result == JevFailed("rate_limited", "rate_limited", result.model_call_id)  # type: ignore[union-attr]
    assert fake.calls == 2
    assert [(r.attempt, r.outcome) for r in await rows(db_session)] == [
        (1, CallOutcome.RATE_LIMITED),
        (2, CallOutcome.RATE_LIMITED),
    ]


async def test_retry_then_answer_is_mapped_and_probabilities_are_audited(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    fake = ScriptedJev(rate_limited(), response(example_answers()))
    result = await jev(sessions, settings, fake).interpret(
        ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef()
    )
    assert isinstance(result, JevAnswered) and result.result.parsed is not None
    audit = await rows(db_session)
    assert [(r.provider, r.role, r.outcome) for r in audit] == [
        ("typesafe", "interpret", CallOutcome.RATE_LIMITED),
        ("typesafe", "interpret", CallOutcome.OK),
    ]
    ok = audit[1]
    assert result.result.model_call_id == ok.id and ok.prompt_version == "jev_questions@v1"
    assert ok.response is not None
    assert ok.response["role_n1"]["probabilities"] == {"strike": 0.97}  # A.3 rule 4
    assert ok.tokens_in == 900
    assert ok.request["state"]["message"]["text"] == EXAMPLE


async def test_disabled_jev_makes_no_call(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fake = ScriptedJev()
    off = JevInterpreter(
        sessions,
        settings.model_copy(update={"typesafe_api_key": SecretStr("k")}),
        lambda: CFG.model_copy(update={"jev": JevConfig(enabled=False)}),
        FixedClock(datetime(2026, 9, 28, tzinfo=UTC)),
        client=fake,  # type: ignore[arg-type]
    )
    result = await off.interpret(ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef())
    assert result == JevFailed("disabled") and fake.calls == 0


async def ask(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, fake: ScriptedJev, **kw: Any
) -> JevAnswered | JevFailed:
    lot_ids = kw.pop("lot_ids", frozenset())
    return await jev(sessions, settings, fake, **kw).interpret(
        ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef(), lot_ids=lot_ids
    )


async def test_a_slow_request_is_a_timeout_not_a_hang(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    fake = ScriptedJev(response(example_answers()), delay=0.3)
    result = await ask(sessions, settings, fake, timeout_ms=50, budget_ms=1000)
    assert isinstance(result, JevFailed) and result.kind == "timeout"
    [row] = await rows(db_session)
    assert row.outcome == CallOutcome.TIMEOUT and row.latency_ms < 250


async def test_a_full_limiter_is_a_queue_timeout(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    """The limiter wait has its own clock: it never eats the request's time."""
    slow = ScriptedJev(*[response(example_answers()) for _ in range(5)], delay=0.3)
    interp = jev(sessions, settings, slow, queue_timeout_ms=20)
    calls = [
        interp.interpret(ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef()) for _ in range(5)
    ]
    results = await asyncio.gather(*calls)  # 4 limiter slots (the default) for 5 calls
    kinds = sorted(r.kind if isinstance(r, JevFailed) else "ok" for r in results)
    assert kinds == ["ok", "ok", "ok", "ok", "queue_timeout"]
    outcomes = sorted(r.outcome for r in await rows(db_session))
    assert outcomes.count(CallOutcome.QUEUE_TIMEOUT) == 1


async def test_a_429_is_not_retried_when_the_budget_cannot_hold_another_request(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fake = ScriptedJev(rate_limited(), response(example_answers()))
    result = await ask(
        sessions, settings, fake, timeout_ms=700, queue_timeout_ms=250, budget_ms=950
    )
    assert isinstance(result, JevFailed) and result.kind == "rate_limited" and fake.calls == 1


@pytest.mark.parametrize(
    ("override", "problem"),
    [
        ({"ticker": c("t9", 0.99)}, "ticker: 't9' was not offered"),
        ({"links_to": c("6b7a3c1e-0000-4000-8000-000000000000", 0.99)}, "links_to:"),
        ({"msg_type": c("entry", math.nan)}, "msg_type: confidence"),
        ({"candle_close": n(1.5)}, "candle_close: expected a yes probability"),
        ({"right": n(0.5)}, "right: expected a choice answer"),
    ],
)
async def test_an_invalid_answer_is_a_model_failure_not_an_exception(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    db_session: AsyncSession,
    override: dict[str, Any],
    problem: str,
) -> None:
    fake = ScriptedJev(response(example_answers(**override)))
    result = await ask(sessions, settings, fake)
    assert isinstance(result, JevFailed) and result.kind == "invalid"
    assert result.detail.startswith(problem), result.detail
    [row] = await rows(db_session)
    assert row.outcome == CallOutcome.SCHEMA_INVALID and row.id == result.model_call_id
    assert row.response is not None and row.response["invalid"] == result.detail


async def test_a_missing_answer_is_invalid(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    answers = example_answers()
    del answers["exit_intent"]
    result = await ask(sessions, settings, ScriptedJev(response(answers)))
    assert result == JevFailed("invalid", "exit_intent: no answer", result.model_call_id)  # type: ignore[union-attr]


async def test_a_scope_violation_sends_nothing(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    """A user lot id in the state: blocked before the request, audited, a safety failure."""
    fake = ScriptedJev(response(example_answers()))
    result = await ask(sessions, settings, fake, lot_ids=frozenset({"spy"}))
    assert isinstance(result, JevFailed) and result.kind == "scope_violation"
    assert fake.calls == 0
    [row] = await rows(db_session)
    assert row.outcome == CallOutcome.SCOPE_BLOCKED and "state" not in row.request
