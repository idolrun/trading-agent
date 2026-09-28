"""Config validation of the model registry (TRD §4.8 rule 5)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import create_session_factory
from app.evalkit.models import EvalRun, EvalRunKind
from app.runtime_config.models import Actor
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, RoleConfig, RuntimeConfig
from app.runtime_config.service import ConfigInvalid, ConfigService
from tests.integration.conftest import _url

NOW = datetime(2026, 9, 28, tzinfo=UTC)


@pytest.fixture
async def engine(owner_engine: object) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield engine
    await engine.dispose()


async def gate_run(db: AsyncSession, model_ref: str, passed: bool = True) -> uuid.UUID:
    run = EvalRun(
        kind=EvalRunKind.REPLAY_GATE,
        model_ref=model_ref,
        metrics={},
        gate_passed=passed,
        started_at=NOW,
        finished_at=NOW,
    )
    db.add(run)
    await db.commit()
    return run.eval_run_id


def config(*entries: ChainEntry) -> RuntimeConfig:
    return RuntimeConfig(llm=LlmConfig(roles=LlmRoles(extract=RoleConfig(chain=list(entries)))))


async def update(service: ConfigService, cfg: RuntimeConfig) -> None:
    await service.update(cfg, expected_version=0, actor=Actor.CLI, reason="test")


async def test_gated_two_vendor_chain_is_accepted(
    engine: AsyncEngine, db_session: AsyncSession
) -> None:
    a = await gate_run(db_session, "anthropic:claude-sonnet-5:extract@v1")
    b = await gate_run(db_session, "openai:gpt-test:extract@v1")
    await update(
        ConfigService(create_session_factory(engine)),
        config(
            ChainEntry(
                provider="anthropic", model="claude-sonnet-5", prompt="extract@v1", gate_run=a
            ),
            ChainEntry(provider="openai", model="gpt-test", prompt="extract@v1", gate_run=b),
        ),
    )


async def test_entry_without_passing_gate_run_is_rejected(
    engine: AsyncEngine, db_session: AsyncSession
) -> None:
    a = await gate_run(db_session, "anthropic:claude-sonnet-5:extract@v1")
    failed = await gate_run(db_session, "openai:gpt-test:extract@v1", passed=False)
    wrong_prompt = await gate_run(db_session, "openai:gpt-test:extract@v0")
    service = ConfigService(create_session_factory(engine))
    claude = ChainEntry(
        provider="anthropic", model="claude-sonnet-5", prompt="extract@v1", gate_run=a
    )
    for run in (None, failed, wrong_prompt):
        gpt = ChainEntry(provider="openai", model="gpt-test", prompt="extract@v1", gate_run=run)
        with pytest.raises(ConfigInvalid, match="openai:gpt-test:extract@v1 has no passing gate"):
            await update(service, config(claude, gpt))


async def test_single_vendor_extract_chain_is_rejected_even_when_ungated_is_allowed(
    engine: AsyncEngine,
) -> None:
    service = ConfigService(create_session_factory(engine), allow_ungated_models=True)
    one_vendor = config(
        ChainEntry(provider="anthropic", model="claude-sonnet-5", prompt="extract@v1"),
        ChainEntry(provider="anthropic", model="claude-haiku-4-5", prompt="extract@v1"),
    )
    with pytest.raises(ConfigInvalid, match="two independent vendors"):
        await update(service, one_vendor)
    await update(
        service,
        config(
            ChainEntry(provider="anthropic", model="claude-sonnet-5", prompt="extract@v1"),
            ChainEntry(provider="deepseek", model="deepseek-chat", prompt="extract@v1"),
        ),
    )
