"""Evalkit fixtures: labeled events on dev/test days with recorded (fixture) interpretations."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from app.db import create_session_factory, in_transaction
from app.evalkit.labels import add_label
from app.evalkit.models import LabelSource, Split
from app.evalkit.split import split_for
from app.graph.message_graph import GraphDeps
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope
from app.ingest.adapter import to_envelope
from app.interpret.interpreter import FixtureInterpreter, Interpreter
from app.interpret.signal import ParsedSignal, ParsedSignals
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory, Harness, option_entry
from tests.messages import PROVIDER_A, create

GOOGL_TEXT = "Bought GOOGL 357.5C 10/2 @ 0.71"
GOOGL_LABEL = {
    "msg_type": "entry",
    "instrument": "option",
    "fields": {
        "underlying": "GOOGL",
        "expiry_token": "10/2",
        "strike": "357.5",
        "right": "call",
        "entry_price": "0.71",
    },
}
CHATTER_LABEL = {"msg_type": "chatter"}


def googl(strike: str = "357.5") -> ParsedSignal:
    return option_entry("GOOGL", strike, f"{strike}C", "10/2", "0.71")


def day_in(split: Split, after: date = date(2026, 9, 1)) -> date:
    day = after
    while split_for(day) != split:
        day += timedelta(days=1)
    return day


def at(day: date, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, 15, minute, tzinfo=UTC)  # 11:00 ET


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path)


@dataclass
class Corpus:
    h: Harness
    envelopes: list[Envelope]

    async def add(
        self,
        text: str,
        recorded: list[ParsedSignal],
        label: list[dict[str, Any]],
        *,
        when: datetime,
        author: int = PROVIDER_A,
        link: dict[str, Any] | None = None,
    ) -> Envelope:
        """A stored event (never queued), its recorded interpretation and its label."""
        env = to_envelope(create(when, text, author=author), when)
        answer = ParsedSignals(signals=recorded, interpreter="llm")
        (self.h.fixtures / f"{env.key.message_id}.json").write_text(answer.model_dump_json())
        body: dict[str, Any] = {"signals": [{"idx": i, **s} for i, s in enumerate(label)]}
        if link:
            body["link"] = link
        async with in_transaction(self.h.factory, None) as db:
            await inbox.store_event(db, env)
            await add_label(db, env.key, body, source=LabelSource.MANUAL, session_id=None)
        self.envelopes.append(env)
        return env


@pytest.fixture
def corpus(h: Harness) -> Corpus:
    return Corpus(h, [])


def replay_deps(h: Harness, interpreter: Interpreter | None = None) -> GraphDeps:
    """The interpret side only: no approvals, notes, relink or corrections."""
    deps = h.processor(fixture=True).deps
    return replace(deps, corrections=None, fixture=interpreter or FixtureInterpreter(h.fixtures))


class Spy(FixtureInterpreter):
    """Records what the interpreter was shown."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.seen: list[tuple[str, list[str]]] = []  # (message id, provider book summaries)

    async def interpret(self, ctx: Any, candidates: Any) -> ParsedSignals:
        self.seen.append((ctx.message.id, [b.summary for b in ctx.provider_book]))
        return await super().interpret(ctx, candidates)


async def count(db: AsyncSession, model: type[SQLModel]) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0
