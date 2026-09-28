"""S4 Jev bench seed (OD-8 go/no-go: p95 ≤ 300 ms). Skipped by default:

    LIVE_JEV_MODEL=<jev model> uv run pytest -m live tests/live/test_jev_bench.py -s

Needs TYPESAFE_API_KEY. Prints p50/p95 latency and msg_type accuracy on synthetic messages;
the 100-message labeled bench comes with the eval kit (F048)."""

import os
import statistics
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.jev.client import JevInterpreter
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import JevConfig
from tests.integration.conftest import _url
from tests.jev_helpers import CFG
from tests.live.test_llm_providers import MESSAGES
from tests.llm_helpers import ctx

pytestmark = pytest.mark.live


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_jev_latency_and_msg_type_accuracy(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    model = os.environ.get("LIVE_JEV_MODEL")
    if model is None or settings.typesafe_api_key is None:
        pytest.skip("set LIVE_JEV_MODEL and TYPESAFE_API_KEY")
    cfg = CFG.model_copy(update={"jev": JevConfig(model=model, enabled=True, timeout_ms=5000)})
    jev = JevInterpreter(sessions, settings, lambda: cfg, FixedClock(datetime.now(UTC)))
    latencies, correct = [], 0
    for _ in range(4):  # 20 calls: enough for a rough p95
        for text, expected in MESSAGES:
            t0 = time.monotonic()
            result = await jev.interpret(ctx(text), find_candidates(text), ref=CallRef())
            latencies.append((time.monotonic() - t0) * 1000)
            assert result is not None, text
            correct += result.msg_type is expected
    p95 = statistics.quantiles(latencies, n=20)[-1]
    print(
        f"jev p50={statistics.median(latencies):.0f}ms p95={p95:.0f}ms "
        f"msg_type accuracy={correct}/{len(latencies)}"
    )
