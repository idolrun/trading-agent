"""F037: A3 ("take half") delivered before A1 (entry) — TRD §12, AC-18, FR-1b: never a silent
feed-only, and never an automatic change."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlmodel import col

from app.cards.models import Card, CardAction, CardSource, CardState
from app.db import in_transaction
from app.inbox.envelope import Envelope
from app.inbox.models import InboxItem
from app.interpret.models import LinkMethod, LinkOutcome, MessageLink
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import ProviderPosition
from app.pipeline.apply import signal_id_for
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.pipeline.harness import T0, follow_up, opened_position, option_entry
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph


async def a3_then_late_a1(w: World, target: object = None) -> tuple[Envelope, Envelope]:
    a1 = await w.h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    a3 = await w.h.message(
        "take half",
        [follow_up("trim", target or opened_position(a1), exit_fraction="half")],
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_A,
    )
    await w.h.process(a3, processor=w.processor)  # the provider book is still empty
    return a1, a3


async def links(w: World, env: Envelope) -> list[MessageLink]:
    async with w.h.factory() as db:
        rows = await db.scalars(
            select(MessageLink)
            .where(col(MessageLink.signal_id) == signal_id_for(env.key, 0))
            .order_by(col(MessageLink.id))
        )
        return list(rows.all())


async def relink_cards(w: World) -> list[Card]:
    async with w.h.factory() as db:
        rows = await db.scalars(
            select(Card).where(
                col(Card.source) == CardSource.SYSTEM, col(Card.action) == CardAction.REVIEW
            )
        )
        return list(rows.all())


async def test_late_entry_proposes_relink_and_approval_applies_it(w: World) -> None:
    a1, a3 = await a3_then_late_a1(w)
    assert [lk.outcome for lk in await links(w, a3)] == [LinkOutcome.NONE]
    await w.h.process(a1, late=True, processor=w.processor)

    [card] = await relink_cards(w)
    assert card.state == CardState.PENDING and card.provider_position_id == opened_position(a1)
    assert card.proposal["relink_signal_id"] == str(signal_id_for(a3.key, 0))
    assert len(await links(w, a3)) == 1  # nothing automatic without approval

    await w.decide(card.card_id)
    none, relinked = await links(w, a3)
    assert relinked.outcome == LinkOutcome.LINKED and relinked.method == LinkMethod.USER_CORRECTION
    assert relinked.supersedes_link_id == none.id
    async with w.h.factory() as db:
        position = await db.get(ProviderPosition, opened_position(a1))
        a3_review = await db.scalar(
            select(Card).where(col(Card.signal_id) == signal_id_for(a3.key, 0))
        )
    assert position is not None and position.remaining_fraction == Decimal("0.5")
    assert a3_review is not None and a3_review.state == CardState.INVALIDATED
    assert await w.orders() == []  # the re-link itself never trades


async def test_relink_approval_runs_the_normal_flow_for_the_users_lot(w: World) -> None:
    a1, _ = await a3_then_late_a1(w)
    await w.h.process(a1, late=True, processor=w.processor)
    async with in_transaction(w.h.factory, None) as db:
        await ledger.record_lot(
            db,
            apply_fill(None, "buy", 4, Decimal("0.74")),
            instrument=GOOGL,
            provider_position_id=opened_position(a1),
            provider_id=PROVIDER_A,
        )
    [card] = await relink_cards(w)
    await w.decide(card.card_id)
    async with w.h.factory() as db:
        trim = await db.scalar(
            select(Card).where(
                col(Card.dedupe_key).startswith("relink:") & (col(Card.action) == CardAction.TRIM)
            )
        )
    assert trim is not None and trim.state == CardState.PENDING
    assert trim.proposal["qty"] is None and trim.lot_id is not None
    assert await w.thread_next(trim.card_id) == ("await_approval",)


async def test_sole_position_relink_is_proposed_whatever_the_model_hinted(w: World) -> None:
    """F077: after the late entry the lane holds one position, so the recheck links by code even
    though the model's hint pointed nowhere; still only a proposal until approved."""
    a1, a3 = await a3_then_late_a1(w, target=uuid.uuid4())
    await w.h.process(a1, late=True, processor=w.processor)
    [card] = await relink_cards(w)
    assert card.provider_position_id == opened_position(a1)
    assert len(await links(w, a3)) == 1


async def test_unchanged_recheck_creates_nothing(w: World) -> None:
    """Two positions were open when A3 came; the late A1 makes three: still ambiguous."""
    a1 = await w.h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )
    others = [
        await w.h.message(
            text,
            [option_entry(*fields)],
            at=T0 + timedelta(seconds=n),
            author=PROVIDER_A,
        )
        for n, (text, fields) in enumerate(
            [
                ("SPY 580p 9/28 2.30", ("SPY", "580", "580p", "9/28", "2.30")),
                ("QQQ 495p 9/28 .98", ("QQQ", "495", "495p", "9/28", ".98")),
            ],
            start=1,
        )
    ]
    for env in others:
        await w.h.process(env, processor=w.processor)
    a3 = await w.h.message(
        "take half",
        [follow_up("trim", uuid.uuid4(), exit_fraction="half")],
        at=T0 + timedelta(seconds=10),
        author=PROVIDER_A,
    )
    await w.h.process(a3, processor=w.processor)
    assert [lk.outcome for lk in await links(w, a3)] == [LinkOutcome.AMBIGUOUS]
    await w.h.process(a1, late=True, processor=w.processor)
    assert await relink_cards(w) == []
    assert len(await links(w, a3)) == 1


async def test_backfilled_late_events_recheck_once_when_the_lane_drains(w: World) -> None:
    """F089: backfill makes every event late; the re-check runs once per lane after the
    backfill (dispatcher idle hook -> flush), not after each event under the lane slot."""
    a1, a3 = await a3_then_late_a1(w)
    backfilled = a1.model_copy(update={"backfilled": True})
    await w.h.process(backfilled, late=True, processor=w.processor)
    assert await relink_cards(w) == []  # deferred
    relink = w.approvals.relink
    assert relink is not None
    created = await relink.flush(a1.lane)
    [card] = await relink_cards(w)
    assert created == [card.card_id]
    assert card.proposal["relink_signal_id"] == str(signal_id_for(a3.key, 0))
    assert await relink.flush(a1.lane) == []  # nothing left


async def test_a_restart_restores_the_deferred_recheck(w: World) -> None:
    a1, _ = await a3_then_late_a1(w)
    await w.h.process(a1.model_copy(update={"backfilled": True}), late=True, processor=w.processor)
    async with in_transaction(w.h.factory, None) as db:  # what a backfilled row looks like
        await db.execute(
            update(InboxItem)
            .where(col(InboxItem.message_id) == a1.key.message_id)
            .values(backfilled=True)
        )
    relink = w.approvals.relink
    assert relink is not None
    relink._pending.clear()  # the process died before the lane drained
    assert await relink.restore_pending(T0 - timedelta(hours=8)) == [a1.lane]
    assert len(await relink.flush(a1.lane)) == 1
