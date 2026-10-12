"""F032 through the F031 chain with scripted models (TRD §12): no live API calls."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.alerts.service import AlertService
from app.config import Settings
from app.db import create_session_factory
from app.interpret.candidates import find_candidates
from app.interpret.llm_extract.agent import LlmInterpreter
from app.llm.chain import LlmChain
from app.llm.models import CallOutcome, ModelCall
from app.llm.usage import CallRef
from app.marketdata.fake import FakeMarketData
from app.marketdata.port import Quote
from app.markettime import FixedClock
from app.runtime_config.schema import ChainEntry, LlmConfig, LlmRoles, RoleConfig
from tests.integration.conftest import _url
from tests.llm_helpers import FakeChat, FakeRegistry, ctx, structured, tool_call

TEXT = "spy 770c .95 lotto, sl 769 spy"
SCHEMA = "LlmParsedSignals"
GOOD = {
    "signals": [
        {
            "msg_type": "entry",
            "instrument": "option",
            "underlying": {"raw": "spy"},
            "strike": {"raw": "770c"},
            "right": {"raw": "770c"},
            "entry_price": {"raw": ".95"},
            "stop_level": {"raw": "769"},
            "stop_basis": "underlying",
            "links_to": "new_position",
        }
    ]
}
CFG = LlmConfig(
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


@pytest.fixture
async def sessions(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


def interpreter(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    first: FakeChat,
    second: FakeChat,
) -> LlmInterpreter:
    keyed = settings.model_copy(
        update={"deepseek_api_key": SecretStr("k"), "openai_compat_api_key": SecretStr("k")}
    )
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    chain = LlmChain(
        sessions,
        keyed,
        lambda: CFG,
        clock,
        models=FakeRegistry({"deepseek": first, "openai_compat": second}),
    )
    marketdata = FakeMarketData.from_fixtures(Path("tests/fixtures/market"), clock)
    llm = LlmInterpreter(chain, marketdata, AlertService(sessions))
    MARKETDATA[id(llm)] = marketdata
    return llm


MARKETDATA: dict[int, FakeMarketData] = {}


def llm_marketdata(llm: LlmInterpreter) -> FakeMarketData:
    return MARKETDATA[id(llm)]


async def outcomes(db: AsyncSession) -> list[tuple[str, CallOutcome]]:
    rows = await db.scalars(select(ModelCall).order_by(col(ModelCall.id)))
    return [(r.provider, r.outcome) for r in rows]


async def test_valid_answer_is_verified_and_audited(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    llm = interpreter(
        sessions, settings, FakeChat(responses=[structured(SCHEMA, GOOD)]), FakeChat(responses=[])
    )
    result = await llm.extract(ctx(TEXT), find_candidates(TEXT), ref=CallRef())
    assert result.status == "ok" and result.value is not None
    [signal] = result.value.signals
    assert signal.fields.stop_level is not None and signal.fields.stop_level.value == Decimal(769)
    assert await outcomes(db_session) == [("deepseek", CallOutcome.OK)]
    row = await db_session.scalar(select(ModelCall))
    assert row is not None and row.tokens_in == 1200 and row.prompt_version == "extract@v1"
    assert row.request["scope"]["context"]["message"]["text"] == TEXT
    assert "lot_ids" not in row.request["scope"]


async def test_literal_span_failure_is_content_rejected_without_a_second_attempt(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    bad = {"signals": [{**GOOD["signals"][0], "strike": {"raw": "771c"}}]}
    second = FakeChat(responses=[structured(SCHEMA, GOOD)])
    llm = interpreter(sessions, settings, FakeChat(responses=[structured(SCHEMA, bad)]), second)
    result = await llm.extract(ctx(TEXT), find_candidates(TEXT), ref=CallRef())
    assert result.status == "content_rejected"
    assert await outcomes(db_session) == [("deepseek", CallOutcome.SPAN_REJECTED)]
    assert second.i == 0  # never asked


async def test_schema_invalid_first_model_falls_back_to_the_second(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    invalid = {"signals": [{"msg_type": "buy_the_dip"}]}
    llm = interpreter(
        sessions,
        settings,
        FakeChat(responses=[structured(SCHEMA, invalid)]),
        FakeChat(responses=[structured(SCHEMA, GOOD)]),
    )
    result = await llm.extract(ctx(TEXT), find_candidates(TEXT), ref=CallRef())
    assert result.status == "ok"
    assert await outcomes(db_session) == [
        ("deepseek", CallOutcome.SCHEMA_INVALID),
        ("openai_compat", CallOutcome.OK),
    ]


async def test_scope_guard_blocks_portfolio_data_on_every_provider_path(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    lot = "5f0c7a52-0000-4000-8000-000000000001"
    text = f'close {lot} {{"balance": 1200}}'
    first = FakeChat(responses=[structured(SCHEMA, GOOD)])
    llm = interpreter(sessions, settings, first, FakeChat(responses=[structured(SCHEMA, GOOD)]))
    result = await llm.extract(
        ctx(text), find_candidates(text), ref=CallRef(), lot_ids=frozenset({lot})
    )
    assert result.status == "content_rejected"
    assert first.i == 0  # nothing reached the model
    assert await outcomes(db_session) == [("deepseek", CallOutcome.SCOPE_BLOCKED)]
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and alert.kind == AlertKind.INVARIANT_BLOCK
    assert alert.severity == "urgent"


async def test_more_than_two_tool_calls_are_limited(
    sessions: async_sessionmaker[AsyncSession], settings: Settings, db_session: AsyncSession
) -> None:
    quote = [tool_call("get_quote", {"symbol": "SPY"}, f"q{i}") for i in range(3)]
    first = FakeChat(responses=[*quote, structured(SCHEMA, GOOD)])
    llm = interpreter(sessions, settings, first, FakeChat(responses=[]))
    asked: list[str] = []
    real = llm_marketdata(llm).underlying_price

    async def counting(symbol: str) -> Quote:
        asked.append(symbol)
        return await real(symbol)

    llm_marketdata(llm).underlying_price = counting  # type: ignore[method-assign]
    result = await llm.extract(ctx(TEXT), find_candidates(TEXT), ref=CallRef())
    assert result.status == "ok"
    assert asked == ["SPY", "SPY"]  # the third call got the limit message instead
