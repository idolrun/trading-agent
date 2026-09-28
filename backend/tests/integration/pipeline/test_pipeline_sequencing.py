"""F026 through the dispatcher: post-time order per provider, poison -> raw review (AC-20)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert
from app.cards.models import Card
from app.db import create_session_factory
from app.dispatcher.service import Dispatcher
from app.inbox.models import InboxItem
from app.ledger.models import ProviderPosition
from app.pipeline.processor import RawReviewPoisonHandler
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    opened_position,
    option_entry,
)
from tests.messages import PROVIDER_A, PROVIDER_B


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


async def run_until_finished(h: Harness, n: int) -> None:
    dispatcher = Dispatcher(
        h.factory,
        config=h.config,
        processor=h.processor(),
        poison=RawReviewPoisonHandler(h.factory, h.alerts),
        clock=h.clock,
        idle_exit_s=0.3,
    )
    await dispatcher.start()
    async with asyncio.timeout(10):
        while True:
            async with h.factory() as db:
                statuses = (await db.scalars(select(InboxItem.status))).all()
            if sum(s in ("done", "stale", "failed") for s in statuses) == n:
                break
            await asyncio.sleep(0.05)
    await dispatcher.stop()


async def test_follow_up_delivered_first_still_applies_in_post_order(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path)
    # A3 arrives before A1; the dispatcher still processes A1 first (post time).
    a1_at, a3_at = T0, T0 + timedelta(seconds=20)
    a1 = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=a1_at,
        author=PROVIDER_A,
    )
    a3 = await h.message(
        "Take half off @ 2.10",
        [follow_up("trim", opened_position(a1), exit_fraction="half")],
        at=a3_at,
        author=PROVIDER_A,
    )
    b2 = await h.message(
        "Bought TSLA 400C 10/2 @ 3.10",
        [option_entry("TSLA", "400", "400C", "10/2", "3.10")],
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_B,
    )
    assert a3 and b2
    await run_until_finished(h, 3)
    positions = {
        p.provider_id: p for p in (await db_session.scalars(select(ProviderPosition))).all()
    }
    assert positions[PROVIDER_A].remaining_fraction == Decimal("0.5")
    assert positions[PROVIDER_B].remaining_fraction == 1


async def test_poison_gives_raw_review_and_next_message_processes(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    h = await Harness.create(factory, tmp_path, poison_retry_limit=3)
    poison = await h.message("??? garbled", None, author=PROVIDER_A)  # no fixture: always fails
    await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=T0 + timedelta(seconds=5),
        author=PROVIDER_A,
    )
    await run_until_finished(h, 2)

    cards = (await db_session.scalars(select(Card).order_by(col(Card.posted_at)))).all()
    assert [(c.action, c.card_type) for c in cards] == [("review", "review"), ("entry", "normal")]
    assert cards[0].proposal["review_reasons"] == ["poison"]
    alert = (await db_session.scalars(select(Alert).where(col(Alert.kind) == "poison"))).one()
    assert alert.card_id == cards[0].card_id and alert.message_id == poison.key.message_id
    statuses = {i.message_id: i.status for i in (await db_session.scalars(select(InboxItem))).all()}
    assert statuses[poison.key.message_id] == "failed"
