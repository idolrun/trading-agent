"""Graph fixtures: the harness with approvals over a shadow broker and a pooled checkpointer."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import ApprovalService
from app.cards.edits import Edits
from app.cards.models import Card, CardAction, CardState
from app.db import create_session_factory
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Order
from app.graph.approval_graph import thread_config
from app.graph.checkpointer import open_pool, saver_and_store
from app.graph.processor import GraphProcessor
from app.inbox.envelope import Envelope
from app.ledger.models import Lot
from app.main import libpq_dsn
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory, Harness, option_entry
from tests.integration.schema.helpers import add_session
from tests.messages import PROVIDER_A

QTY = 13  # the quantity a test user types when the test does not care


async def _never() -> bool:
    return False


@dataclass
class World:
    h: Harness
    broker: SimulatedBroker
    approvals: ApprovalService
    processor: GraphProcessor
    saver: AsyncPostgresSaver
    session_id: uuid.UUID

    async def entry(self) -> tuple[Envelope, uuid.UUID]:
        env = await self.h.message(
            "Bought GOOGL 357.5C 10/2 @ 0.71",
            [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
            author=PROVIDER_A,
        )
        await self.h.process(env, processor=self.processor)
        async with self.h.factory() as db:
            card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
        assert card is not None and card.state == CardState.PENDING
        return env, card.card_id

    async def decide(
        self,
        card_id: uuid.UUID,
        decision: Any = "approve",
        version: int = 1,
        *,
        bare: bool = False,
        **edits: Any,
    ) -> None:
        """The user's decision. An approval of a buy or sell carries the quantity the user
        types (C105): tests that do not care get QTY (a sell: the whole lot); `bare=True`
        sends the approval exactly as given, to test what the backend refuses."""
        if decision == "approve" and not bare and "qty" not in edits:
            card = await self.card(card_id)
            if card.action in (CardAction.ENTRY, CardAction.ADD):
                edits["qty"] = QTY
            elif card.action in (CardAction.TRIM, CardAction.EXIT):
                async with self.h.factory() as db:
                    lot = await db.get(Lot, card.lot_id) if card.lot_id else None
                edits["qty"] = lot.qty if lot is not None else QTY
        await self.approvals.decide(
            card_id,
            decision,
            version,
            Edits(**edits),
            session_id=self.session_id,
            consume_step_up=_never,
        )

    async def card(self, card_id: uuid.UUID) -> Card:
        async with self.h.factory() as db:
            card = await db.get(Card, card_id)
        assert card is not None
        return card

    async def orders(self) -> list[Order]:
        async with self.h.factory() as db:
            return list((await db.scalars(select(Order))).all())

    async def consume(self) -> None:
        """Apply every broker event (fills -> lots), with the bracket hook (F042), until quiet."""
        consumer = BrokerEventConsumer(self.h.factory, [self.broker])
        brackets = self.approvals._executor.brackets
        if brackets is not None:
            consumer.on_fill.append(brackets.on_fill)
        while events := self.broker.pending_events():
            for event in events:
                await consumer.handle(event)

    async def elapse(self, seconds: float) -> None:
        await self.consume()

    async def exits_done(self) -> None:
        """Let every running exit re-price loop finish."""
        exit_policy = self.approvals._executor.exit
        if exit_policy is not None:
            await asyncio.gather(*list(exit_policy.loops.values()))
        await self.consume()

    async def thread_next(self, card_id: uuid.UUID) -> tuple[str, ...]:
        graph = self.approvals._graph
        snap = await graph.aget_state(thread_config(card_id))
        assert snap.created_at is not None, "approval thread was never started"
        return tuple(snap.next)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def w(factory: Factory, tmp_path: Path, db_session: AsyncSession) -> AsyncIterator[World]:
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    await pool.open()
    try:
        saver, _ = saver_and_store(pool)
        h = await Harness.create(factory, tmp_path)
        broker = SimulatedBroker(h.md, h.clock)
        approvals = h.approval_service(saver, broker)
        world = World(
            h,
            broker,
            approvals,
            h.processor(approvals=approvals),
            saver,
            await add_session(db_session),
        )
        exit_policy = approvals._executor.exit
        if exit_policy is not None:
            exit_policy.sleep = world.elapse  # time passing = broker events delivered
        yield world
        if exit_policy is not None:  # no re-price loop outlives its test
            for task in list(exit_policy.loops.values()):
                task.cancel()
            await asyncio.gather(*exit_policy.loops.values(), return_exceptions=True)
    finally:
        await pool.close()
