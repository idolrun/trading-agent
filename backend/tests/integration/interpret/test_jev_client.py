"""F033 client: retry once on 429/529, audit every attempt with probabilities, degrade to None."""

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
from app.interpret.jev.client import JevInterpreter
from app.llm.models import CallOutcome, ModelCall
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import JevConfig
from tests.integration.conftest import _url
from tests.jev_helpers import CFG, EXAMPLE, example_answers
from tests.llm_helpers import ctx

JEV_ON = CFG.model_copy(update={"jev": JevConfig(model="jev-test", enabled=True)})


class ScriptedJev:
    """Stands in for `AsyncTypeSafeClient`: each call pops the next exception or response."""

    def __init__(self, *script: BaseException | SystemOneResponse) -> None:
        self.script = list(script)
        self.calls = 0

    async def system_one(self, **kwargs: Any) -> SystemOneResponse:
        self.calls += 1
        step = self.script.pop(0)
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
    sessions: async_sessionmaker[AsyncSession], settings: Settings, fake: ScriptedJev
) -> JevInterpreter:
    keyed = settings.model_copy(update={"typesafe_api_key": SecretStr("k")})
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    client: AsyncTypeSafeClient = fake  # type: ignore[assignment]
    return JevInterpreter(sessions, keyed, lambda: JEV_ON, clock, client=client)


async def rows(db: AsyncSession) -> list[ModelCall]:
    return list(await db.scalars(select(ModelCall).order_by(col(ModelCall.id))))


async def test_rate_limited_twice_degrades_to_the_slow_path(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    fake = ScriptedJev(rate_limited(), rate_limited())
    result = await jev(sessions, settings, fake).interpret(
        ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef()
    )
    assert result is None and fake.calls == 2
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
    assert result is not None and result.parsed is not None
    audit = await rows(db_session)
    assert [(r.provider, r.role, r.outcome) for r in audit] == [
        ("typesafe", "interpret", CallOutcome.RATE_LIMITED),
        ("typesafe", "interpret", CallOutcome.OK),
    ]
    ok = audit[1]
    assert result.model_call_id == ok.id and ok.prompt_version == "jev_questions@v1"
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
        lambda: CFG,  # jev.enabled defaults to False
        FixedClock(datetime(2026, 9, 28, tzinfo=UTC)),
        client=fake,  # type: ignore[arg-type]
    )
    assert await off.interpret(ctx(EXAMPLE), find_candidates(EXAMPLE), ref=CallRef()) is None
    assert fake.calls == 0
