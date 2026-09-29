"""Test-only control plane for the browser E2E suite (F062, frontend/e2e).

Mounted by `create_app` only when `environment == "test"` (never in development or production,
asserted in tests/api/test_testing_routes.py) and left out of the OpenAPI contract. No
session needed: the environment gate is the protection.
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.errors import ApiError
from app.api.schemas.base import CamelModel
from app.instruments import OptionContract
from app.interpret.signal import ParsedSignals
from app.marketdata.fake import FakeMarketData, MarketFixture
from app.markettime import ControlledClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig

router = APIRouter(prefix="/test", tags=["test"], include_in_schema=False)

# Structural rows survive a reset (flags are cleared instead); `users` too unless asked.
KEEP_TABLES = {"alembic_version", "system_flags"}
GRAPH_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "store")


def _clock(request: Request) -> ControlledClock:
    clock = request.app.state.clock
    if not isinstance(clock, ControlledClock):
        raise ApiError("the app clock is not controllable", "invalid", 409)
    return clock


class ClockIn(CamelModel):
    at: datetime | None = None
    advance_s: float | None = None


class ClockOut(CamelModel):
    now: datetime


@router.get("/clock")
async def get_clock(request: Request) -> ClockOut:
    return ClockOut(now=request.app.state.clock.now())


@router.post("/clock")
async def set_clock(body: ClockIn, request: Request) -> ClockOut:
    """Set (`at`) and/or move (`advanceS`) the one app clock; timers fire on their next poll."""
    clock = _clock(request)
    if body.at is not None:
        clock.set(body.at)
    if body.advance_s is not None:
        clock.advance(timedelta(seconds=body.advance_s))
    if timers := getattr(request.app.state, "timers", None):
        timers.wake()  # due timers fire now, not on the next 1 s poll
    return ClockOut(now=clock.now())


class QuoteIn(CamelModel):
    contract: OptionContract
    bid: Decimal | None
    ask: Decimal | None


class MarketIn(CamelModel):
    fixtures: list[MarketFixture] = Field(default_factory=list)
    quotes: list[QuoteIn] = Field(default_factory=list)


@router.post("/market", status_code=204)
async def market(body: MarketIn, request: Request) -> None:
    """Load chains/underlyings and script option quotes on the fake market data. The shadow
    broker fills a marketable order at these quotes."""
    md: FakeMarketData = request.app.state.marketdata
    for fixture in body.fixtures:
        md.load(fixture)
    for quote in body.quotes:
        md.set_quote(quote.contract, quote.bid, quote.ask)


@router.put("/interpretations/{message_id}", status_code=204)
async def interpretation(message_id: int, body: ParsedSignals, request: Request) -> None:
    """What the FixtureInterpreter returns for `message_id` (write it before the message)."""
    directory = request.app.state.settings.interpretation_fixtures_dir
    if directory is None:
        raise ApiError("INTERPRETATION_FIXTURES_DIR is not set", "invalid", 409)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{message_id}.json").write_text(body.model_dump_json())


class ResetIn(CamelModel):
    at: datetime | None = None
    config: dict[str, Any] | None = None  # RuntimeConfig document; None = unconfigured
    keep_users: bool = True


def _where(url: str) -> tuple[str | None, int | None, str | None]:
    parsed = make_url(url)
    return parsed.host, parsed.port, parsed.database


@router.post("/reset", status_code=204)
async def reset(body: ResetIn, request: Request) -> None:
    """Empty every table (as the owner role), clear flags, the fake broker and market data,
    seed the runtime config. Leaves the running services in place."""
    state = request.app.state
    owner_url = str(state.settings.database_owner_url)
    if _where(owner_url) != _where(str(state.settings.database_url)):
        raise ApiError("DATABASE_OWNER_URL is not the app's database: refusing", "invalid", 409)
    if body.at is not None:
        _clock(request).set(body.at)
    for task in list(state.exits.loops.values()):  # re-price loops of the last test
        task.cancel()
    await asyncio.gather(*state.exits.loops.values(), return_exceptions=True)

    keep = KEEP_TABLES | ({"users"} if body.keep_users else set())
    engine = create_async_engine(owner_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            tables = (
                await conn.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).all()
            truncate = [t for t in tables if t not in keep]
            await conn.execute(text(f"TRUNCATE {', '.join(truncate)} RESTART IDENTITY CASCADE"))
            await conn.execute(
                text(f"TRUNCATE {', '.join(f'langgraph.{t}' for t in GRAPH_TABLES)}")
            )
            await conn.execute(text("UPDATE system_flags SET active = false, reason = NULL"))
    finally:
        await engine.dispose()

    for broker in (state.broker, state.simulated):  # fakes; card/client ids repeat across tests
        broker.forget()
    md: FakeMarketData = state.marketdata
    for table in (md.quotes, md.underlyings, md.chains, md.option_ticks):
        table.clear()
    md.optionable.clear()
    now = state.clock.now()
    state.feed_health.last_message_at = state.feed_health.last_heartbeat_at = now
    await state.config_service.load()
    if body.config is not None:
        await state.config_service.update(
            RuntimeConfig.model_validate(body.config),
            expected_version=0,
            actor=Actor.CLI,
            reason="e2e reset",
        )
