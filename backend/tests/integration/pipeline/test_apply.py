"""F026: message event -> one apply transaction (signals, links, ledger, cards, timers)."""

import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert
from app.cards.models import Card, CardAction, CardEvent, CardState, CardType
from app.db import create_session_factory, in_transaction
from app.inbox.envelope import Envelope
from app.inbox.models import InboxItem
from app.instruments import OptionContract
from app.interpret.models import MessageLink, Signal
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import ProviderPosition, ProviderPositionEvent
from app.pipeline.apply import signal_id_for
from app.system.models import FlagName, SystemFlag
from app.timers.models import Timer
from app.validate.service import ValidationService
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    lit,
    opened_position,
    option_entry,
)
from tests.messages import PROVIDER_A, PROVIDER_B

S = timedelta(seconds=1)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(factory: Factory, tmp_path: Path) -> Harness:
    return await Harness.create(factory, tmp_path)


async def count(db: AsyncSession, model: type) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def googl(h: Harness) -> Envelope:
    return await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        author=PROVIDER_A,
    )


async def test_planning_lines_carry_the_signal_id(
    h: Harness, monkeypatch: pytest.MonkeyPatch, log_lines: list[dict[str, Any]]
) -> None:
    validate = ValidationService.validate

    async def logged(self: ValidationService, *args: Any, **kwargs: Any) -> Any:
        logging.getLogger("test").info("validating")
        return await validate(self, *args, **kwargs)

    monkeypatch.setattr(ValidationService, "validate", logged)
    env = await googl(h)
    await h.process(env)
    [line] = [x for x in log_lines if x["msg"] == "validating"]
    assert line["signal_id"] == str(signal_id_for(env.key, 0))


async def test_ac18_links_stay_within_each_provider(h: Harness, db_session: AsyncSession) -> None:
    a1 = await googl(h)
    b2 = await h.message(
        "Bought TSLA 400C 10/2 @ 3.10",
        [option_entry("TSLA", "400", "400C", "10/2", "3.10")],
        at=T0 + 10 * S,
        author=PROVIDER_B,
    )
    pa, pb = opened_position(a1), opened_position(b2)
    a3 = await h.message(
        "Take half off @ 2.10",
        [follow_up("trim", pa, exit_fraction="half")],
        at=T0 + 20 * S,
        author=PROVIDER_A,
    )
    b4 = await h.message(
        "Stop 1.50",
        [follow_up("stop_move", pb, stop_level=lit(Decimal("1.50"), "1.50"))],
        at=T0 + 30 * S,
        author=PROVIDER_B,
    )
    for env in (a1, b2, a3, b4):
        await h.process(env)

    links = {
        (await db_session.get(Signal, link.signal_id)).message_id: link  # type: ignore[union-attr]
        for link in (await db_session.scalars(select(MessageLink))).all()
    }
    assert links[a3.key.message_id].provider_position_id == pa
    assert links[b4.key.message_id].provider_position_id == pb
    assert {links[m].provider_id for m in (a1.key.message_id, a3.key.message_id)} == {PROVIDER_A}
    a = await db_session.get(ProviderPosition, pa)
    b = await db_session.get(ProviderPosition, pb)
    assert a is not None and (a.status, a.remaining_fraction) == ("trimmed", Decimal("0.5"))
    assert b is not None and b.stop_level == Decimal("1.50") and b.remaining_fraction == 1

    cards = {c.provider_position_id: c for c in (await db_session.scalars(select(Card))).all()}
    assert set(cards) == {pa, pb}  # entries only: the user holds no lots, follow-ups feed only
    entry = cards[pa]
    assert (
        entry.action == CardAction.ENTRY and entry.proposal["qty"] is None
    )  # C105: the user enters it
    # 7.2: a newer provider message on the same position invalidates the pending entry card
    assert {c.state for c in cards.values()} == {CardState.INVALIDATED}
    timers = (await db_session.scalars(select(Timer))).all()
    assert {t.card_id for t in timers} == {cards[pa].card_id, cards[pb].card_id}
    statuses = set((await db_session.scalars(select(InboxItem.status))).all())
    assert statuses == {"done"}


async def test_rerun_writes_nothing_twice(h: Harness, db_session: AsyncSession) -> None:
    a1 = await googl(h)
    await h.process(a1)
    before = [await count(db_session, m) for m in (Signal, Card, CardEvent, ProviderPosition)]
    await h.process(a1, attempt=2)
    after = [await count(db_session, m) for m in (Signal, Card, CardEvent, ProviderPosition)]
    assert before == after == [1, 1, 1, 1]
    assert await count(db_session, ProviderPositionEvent) == 1


async def test_stale_entry_updates_ledger_without_card(
    h: Harness, db_session: AsyncSession
) -> None:
    a1 = await googl(h)
    await h.process(a1, stale=True)
    assert await count(db_session, ProviderPosition) == 1
    assert await count(db_session, Card) == 0
    item = (await db_session.scalars(select(InboxItem))).one()
    assert item.status == "stale"


async def test_ambiguous_follow_up_is_review_without_ledger_change(
    h: Harness, db_session: AsyncSession
) -> None:
    a1 = await googl(h)
    a2 = await h.message(
        "Bought META 700P 10/2 @ 0.95",
        [option_entry("META", "700", "700P", "10/2", "0.95")],
        at=T0 + 5 * S,
        author=PROVIDER_A,
    )
    a3 = await h.message(
        "take half off @ 2.10",
        [follow_up("trim", "ambiguous", "0.4", exit_fraction="half")],
        at=T0 + 10 * S,
        author=PROVIDER_A,
    )
    for env in (a1, a2, a3):
        await h.process(env)
    review = (
        await db_session.scalars(select(Card).where(col(Card.card_type) == CardType.REVIEW))
    ).one()
    assert review.action == CardAction.TRIM and review.provider_position_id is None
    assert set(review.proposal["candidate_position_ids"]) == {
        str(opened_position(a1)),
        str(opened_position(a2)),
    }
    positions = (await db_session.scalars(select(ProviderPosition))).all()
    assert {p.remaining_fraction for p in positions} == {1}
    link = (
        await db_session.scalars(select(MessageLink).where(col(MessageLink.outcome) == "ambiguous"))
    ).one()
    assert link.provider_position_id is None and len(link.candidate_position_ids) == 2


async def test_trim_with_a_lot_gets_an_exit_card(h: Harness, db_session: AsyncSession) -> None:
    a1 = await googl(h)
    await h.process(a1)
    pa = opened_position(a1)
    contract = OptionContract(
        underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
    )
    async with in_transaction(h.factory, None) as db:
        await ledger.record_lot(
            db,
            apply_fill(None, "buy", 5, Decimal("0.74")),
            instrument=contract,
            provider_position_id=pa,
            provider_id=PROVIDER_A,
        )
    a3 = await h.message(
        "Take half off @ 2.10",
        [follow_up("trim", pa, exit_fraction="half")],
        at=T0 + 20 * S,
        author=PROVIDER_A,
    )
    await h.process(a3)
    trim = (await db_session.scalars(select(Card).where(col(Card.action) == "trim"))).one()
    assert trim.proposal["qty"] is None and trim.lot_id is not None and trim.state == "pending"


async def test_literal_span_mismatch_gives_raw_review(h: Harness, db_session: AsyncSession) -> None:
    env = await h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71",
        [option_entry("AAPL", "357.5", "357.5C", "10/2", "0.71")],  # AAPL is not in the text
        author=PROVIDER_A,
    )
    await h.process(env)
    card = (await db_session.scalars(select(Card))).one()
    assert (card.action, card.card_type) == ("review", "review")
    assert card.proposal["review_reasons"] == ["literal_span_mismatch"]
    signal = (await db_session.scalars(select(Signal))).one()
    assert signal.msg_type == "unparsed"
    assert await count(db_session, ProviderPosition) == 0


async def test_no_interpreter_configured_gives_a_review_card_not_a_degraded_parser(
    h: Harness, db_session: AsyncSession
) -> None:
    """Before any model is configured (Jev off, empty LLM chain) the LLM is optional: the
    message is a review card saying so (FR-9), and nothing is reported as failing."""
    env = await h.message("Bought GOOGL 357.5C 10/2 @ 0.71 here", None, author=PROVIDER_A)
    await h.process(env, processor=h.processor(fixture=False))
    card = (await db_session.scalars(select(Card))).one()
    assert card.proposal["review_reasons"] == ["needs_review: jev_disabled"]
    flag = await db_session.get(SystemFlag, FlagName.PARSER_DEGRADED)
    assert flag is None or not flag.active
    assert (await db_session.scalars(select(Alert))).all() == []
    assert (await db_session.scalars(select(InboxItem.status))).one() == "done"


async def test_an_obvious_entry_needs_no_model_at_all(h: Harness, db_session: AsyncSession) -> None:
    """F070: with every model off, an obvious entry is still a normal card (fast path)."""
    env = await h.message("Bought GOOGL 357.5C 10/2 @ 0.71", None, author=PROVIDER_A)
    await h.process(env, processor=h.processor(fixture=False))
    card = (await db_session.scalars(select(Card))).one()
    assert (card.action, card.card_type) == (CardAction.ENTRY, CardType.NORMAL)
    assert (await db_session.scalars(select(Signal.interpreter))).one() == "fast"
