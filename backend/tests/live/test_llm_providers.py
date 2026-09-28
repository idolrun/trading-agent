"""Live smoke of the extraction agent per provider (S5 bench seed). Skipped by default:

    LIVE_ANTHROPIC_MODEL=claude-sonnet-5 uv run pytest -m live tests/live/test_llm_providers.py

Needs the provider's API key in `.env` and Postgres (model_calls rows are written).
The labeled 50-message corpus and accuracy scoring come with the eval kit (F048)."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.service import AlertService
from app.config import Settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.llm_extract.agent import LlmInterpreter
from app.interpret.models import MsgType
from app.llm.chain import LlmChain
from app.llm.registry import PROVIDERS, api_key
from app.llm.usage import CallRef
from app.marketdata.fake import FakeMarketData
from app.markettime import SystemClock
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmProvider, LlmRoles, RoleConfig
from tests.integration.conftest import _url
from tests.llm_helpers import ctx

pytestmark = pytest.mark.live

# Synthetic only (C87): never messages from docs/data/.
MESSAGES = [
    ("QQQ 480p 1.20 small size, stop 1.00", MsgType.ENTRY),
    ("spy 770c .95 lotto, sl 769 spy", MsgType.ENTRY),
    ("what a day lol", MsgType.CHATTER),
    ("up 120% on the tsla calls, gg", MsgType.COMMENTARY_PNL),
    ("watching nvda 140c for a break of 138", MsgType.WATCH),
]


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_provider_extracts_synthetic_messages(
    provider: LlmProvider, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    model = os.environ.get(f"LIVE_{provider.upper()}_MODEL")
    if model is None or api_key(settings, provider) is None:
        pytest.skip(f"set LIVE_{provider.upper()}_MODEL and the {provider} API key")
    cfg = LlmConfig(
        roles=LlmRoles(
            extract=RoleConfig(
                deadline_ms=30_000,
                chain=[ChainEntry(provider=provider, model=model, prompt="extract@v1")],
            )
        )
    )
    clock = SystemClock()
    llm = LlmInterpreter(
        LlmChain(sessions, settings, lambda: cfg, clock),
        FakeMarketData.from_fixtures(Path("tests/fixtures/market"), clock),
        AlertService(sessions),
    )
    for text, expected in MESSAGES:
        result = await llm.extract(ctx(text), find_candidates(text), ref=CallRef())
        assert result.status == "ok", (text, result.reason)
        assert result.value is not None
        assert result.value.signals[0].msg_type is expected, text
