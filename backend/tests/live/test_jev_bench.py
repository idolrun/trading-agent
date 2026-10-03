"""S4 Jev bench seed + F079 routing probes against the real model. Skipped by default:

    LIVE_JEV_MODEL=<jev model> uv run pytest -m live tests/live/test_jev_bench.py -s

Needs TYPESAFE_API_KEY. Prints p50/p95 latency and msg_type accuracy on synthetic messages,
then for each F079 probe (conditions, implicit exits) what Jev answered and where the
deterministic router sends it, with and without a position open in the lane. The real-corpus
measurement is the evalkit replay (F082)."""

import os
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.feed_guard import feed_block
from app.interpret.jev.client import JevAnswered, JevInterpreter
from app.interpret.route import route
from app.llm.usage import CallRef
from app.markettime import FixedClock
from app.runtime_config.schema import JevConfig, RuntimeConfig
from tests.integration.conftest import _url
from tests.jev_helpers import CFG
from tests.live.test_llm_providers import MESSAGES
from tests.llm_helpers import ctx

pytestmark = pytest.mark.live

# (text, where a careful reader would send it with a position open)
PROBES = [
    ("if SPY holds 770 then calls", "assemble (conditional setup)"),
    ("watching SPY around 770", "feed_only (watch)"),
    ("SPY looks strong", "feed_only (commentary)"),
    ("if this breaks I'm out", "slow_path (conditional exit)"),
    ("gotta go here", "slow_path (implicit exit)"),
    ("pop it here @everyone", "slow_path (implicit exit)"),
    ("Stopped last 3 have a good weekend @everyone", "slow_path (stopped out)"),
    ("done", "slow_path (maybe an exit)"),
    ("taking the rest", "slow_path (exit)"),
]
OPEN = [(uuid.uuid4(), "SPY 765P exp 2026-10-02, entered 0.54 at 2026-10-02 09:41:00 ET")]


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def live_cfg(settings: Settings) -> RuntimeConfig:
    model = os.environ.get("LIVE_JEV_MODEL")
    if model is None or settings.typesafe_api_key is None:
        pytest.skip("set LIVE_JEV_MODEL and TYPESAFE_API_KEY")
    jev = JevConfig(model=model, enabled=True, timeout_ms=5000, budget_ms=6000)
    return CFG.model_copy(update={"jev": jev})


async def test_jev_latency_and_msg_type_accuracy(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    cfg = live_cfg(settings)
    jev = JevInterpreter(sessions, settings, lambda: cfg, FixedClock(datetime.now(UTC)))
    latencies, correct = [], 0
    for _ in range(4):  # 20 calls: enough for a rough p95
        for text, expected in MESSAGES:
            t0 = time.monotonic()
            outcome = await jev.interpret(ctx(text), find_candidates(text), ref=CallRef())
            latencies.append((time.monotonic() - t0) * 1000)
            assert isinstance(outcome, JevAnswered), (text, outcome)
            correct += outcome.result.msg_type is expected
    p95 = statistics.quantiles(latencies, n=20)[-1]
    print(
        f"jev p50={statistics.median(latencies):.0f}ms p95={p95:.0f}ms "
        f"msg_type accuracy={correct}/{len(latencies)}"
    )


async def test_f079_probes_print_jev_and_the_route(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    cfg = live_cfg(settings)
    jev = JevInterpreter(sessions, settings, lambda: cfg, FixedClock(datetime.now(UTC)))
    for text, expected in PROBES:
        context = ctx(text, book=OPEN)
        candidates = find_candidates(text)
        outcome = await jev.interpret(context, candidates, ref=CallRef())
        assert isinstance(outcome, JevAnswered), (text, outcome)
        result = outcome.result
        condition = result.hints.get("has_prose_condition", {}).get("yes")
        open_route = route(result, cfg, feed_block(text, context, candidates))
        flat_route = route(result, cfg)
        print(
            f"{text!r}: jev={result.msg_type}@{result.msg_type_confidence} "
            f"condition={condition} | open: {open_route.path} {list(open_route.reasons)[:1]} "
            f"| no position: {flat_route.path} | expected (open): {expected}"
        )
        if expected.startswith("slow_path"):  # the safety property: never silenced
            assert open_route.path != "feed_only", text
