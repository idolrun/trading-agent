"""TRD §12 LLM provider table: fallback, no answer shopping, exhaustion, breaker, audit rows."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from langchain_core.messages.ai import UsageMetadata
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.config import Settings
from app.db import create_session_factory
from app.llm.chain import Attempt, AttemptFailed, ContentRejected, LlmChain
from app.llm.models import CallOutcome, ModelCall
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, Price, RoleConfig
from tests.integration.conftest import _url

CLAUDE = ChainEntry(provider="anthropic", model="claude-sonnet-5", prompt="extract@v1")
GPT = ChainEntry(provider="openai", model="gpt-test", prompt="extract@v1")


def llm_config(deadline_ms: int = 3000, cap_ms: int | None = 2000) -> LlmConfig:
    return LlmConfig(
        roles=LlmRoles(
            extract=RoleConfig(
                deadline_ms=deadline_ms, per_attempt_cap_ms=cap_ms, chain=[CLAUDE, GPT]
            )
        ),
        prices={
            "openai:gpt-test": Price(
                input_per_mtok=Decimal(1),
                output_per_mtok=Decimal(4),
                cached_input_per_mtok=Decimal(0),
            )
        },
    )


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"anthropic_api_key": SecretStr("k1"), "openai_api_key": SecretStr("k2")}
    )


def make_chain(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, cfg: LlmConfig
) -> LlmChain:
    return LlmChain(sessions, settings, lambda: cfg, FixedClock(datetime(2026, 9, 28, tzinfo=UTC)))


async def calls(db: AsyncSession) -> list[tuple[int, str, CallOutcome]]:
    rows = await db.scalars(select(ModelCall).order_by(col(ModelCall.id)))
    return [(r.attempt, r.provider, r.outcome) for r in rows]


def answer(by: str) -> Callable[[Attempt], Awaitable[str]]:
    async def attempt_fn(a: Attempt) -> str:
        a.request = {"prompt": "p"}
        if a.entry.provider != by:
            if by == "timeout":
                await asyncio.sleep(10)
            raise AttemptFailed(CallOutcome.SCHEMA_INVALID, "bad json")
        a.usage = UsageMetadata(input_tokens=1000, output_tokens=100, total_tokens=1100)
        return f"answer from {a.entry.provider}"

    return attempt_fn


async def test_primary_timeout_falls_back_to_second_vendor_within_deadline(
    sessions: async_sessionmaker[AsyncSession], keyed: Settings, db_session: AsyncSession
) -> None:
    async def attempt_fn(a: Attempt) -> str:
        if a.entry.provider == "anthropic":
            await asyncio.sleep(10)
        a.usage = UsageMetadata(input_tokens=1000, output_tokens=100, total_tokens=1100)
        return "ok"

    chain = make_chain(sessions, keyed, llm_config(deadline_ms=1000, cap_ms=200))
    result = await chain.run("extract", attempt_fn, ref=CallRef())
    assert result.status == "ok" and result.value == "ok" and result.entry == GPT
    assert await calls(db_session) == [
        (1, "anthropic", CallOutcome.TIMEOUT),
        (2, "openai", CallOutcome.OK),
    ]
    ok = await db_session.scalar(select(ModelCall).where(col(ModelCall.outcome) == "ok"))
    assert ok is not None
    assert (ok.tokens_in, ok.tokens_out, ok.cost_usd) == (1000, 100, Decimal("0.001400"))
    assert ok.prompt_version == "extract@v1" and ok.role == "extract"


async def test_schema_invalid_primary_falls_back(
    sessions: async_sessionmaker[AsyncSession], keyed: Settings, db_session: AsyncSession
) -> None:
    result = await make_chain(sessions, keyed, llm_config()).run(
        "extract", answer("openai"), ref=CallRef()
    )
    assert result.status == "ok"
    assert [o for _, _, o in await calls(db_session)] == [
        CallOutcome.SCHEMA_INVALID,
        CallOutcome.OK,
    ]


async def test_content_rejection_never_falls_back(
    sessions: async_sessionmaker[AsyncSession], keyed: Settings, db_session: AsyncSession
) -> None:
    async def attempt_fn(a: Attempt) -> str:
        raise ContentRejected(CallOutcome.SPAN_REJECTED, "strike 771 not in message")

    result = await make_chain(sessions, keyed, llm_config()).run(
        "extract", attempt_fn, ref=CallRef()
    )
    assert result.status == "content_rejected" and result.entry == CLAUDE
    assert await calls(db_session) == [(1, "anthropic", CallOutcome.SPAN_REJECTED)]


async def test_all_fail_or_deadline_passes_is_exhausted(
    sessions: async_sessionmaker[AsyncSession], keyed: Settings, db_session: AsyncSession
) -> None:
    chain = make_chain(sessions, keyed, llm_config())
    result = await chain.run("extract", answer("nobody"), ref=CallRef())
    assert result.status == "exhausted" and len(result.attempts) == 2

    slow = make_chain(sessions, keyed, llm_config(deadline_ms=150, cap_ms=None))
    result = await slow.run("extract", answer("timeout"), ref=CallRef())
    assert result.status == "exhausted"
    assert [a.outcome for a in result.attempts] == [CallOutcome.TIMEOUT]  # no time left for #2


async def test_open_breaker_skips_provider_and_records_circuit_open(
    sessions: async_sessionmaker[AsyncSession], keyed: Settings, db_session: AsyncSession
) -> None:
    chain = make_chain(sessions, keyed, llm_config())
    for _ in range(5):
        chain.breaker("anthropic").failure()
    result = await chain.run("extract", answer("openai"), ref=CallRef())
    assert result.status == "ok"
    assert await calls(db_session) == [
        (1, "anthropic", CallOutcome.CIRCUIT_OPEN),
        (2, "openai", CallOutcome.OK),
    ]


async def test_provider_without_api_key_is_skipped(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    only_openai = settings.model_copy(
        update={"anthropic_api_key": None, "openai_api_key": SecretStr("k2")}
    )
    result = await make_chain(sessions, only_openai, llm_config()).run(
        "extract", answer("openai"), ref=CallRef()
    )
    assert result.status == "ok"
    assert await calls(db_session) == [(2, "openai", CallOutcome.OK)]
