"""F038 / AC-5: an edit before the trade updates the card and needs re-approval; a correction
after the trade is an alert with no action; deleting the entry message invalidates its card."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.cards.models import Card, CardAction, CardEvent, CardReason, CardState
from app.db import in_transaction
from app.execution.models import Order
from app.instruments import OptionContract
from app.interpret.signal import LinkHint, ParsedSignal, SignalFields
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.models import ProviderPosition
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.pipeline.harness import T0, follow_up, lit, opened_position, option_entry
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
GOOGL_360 = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("360"), right="call"
)


async def cards_by(w: World, **where: object) -> list[Card]:
    async with w.h.factory() as db:
        q = select(Card).order_by(col(Card.created_at))
        for name, value in where.items():
            q = q.where(getattr(Card, name) == value)
        return list((await db.scalars(q)).all())


async def alerts(w: World, kind: AlertKind) -> list[Alert]:
    async with w.h.factory() as db:
        return list((await db.scalars(select(Alert).where(col(Alert.kind) == kind))).all())


async def position(w: World, env: object) -> ProviderPosition:
    async with w.h.factory() as db:
        row = await db.get(ProviderPosition, opened_position(env))  # type: ignore[arg-type]
    assert row is not None
    return row


async def test_edit_before_trade_invalidates_and_needs_fresh_approval(w: World) -> None:
    a1, first = await w.entry()
    edit = await w.h.edit(
        a1,
        "Bought GOOGL 360C 10/2 @ 0.47",
        [option_entry("GOOGL", "360", "360C", "10/2", "0.47")],
        at=T0 + timedelta(seconds=20),
    )
    await w.h.process(edit, processor=w.processor)

    old = await w.card(first)
    assert old.state == CardState.INVALIDATED
    async with w.h.factory() as db:
        reason = await db.scalar(
            select(CardEvent.reason_code).where(
                col(CardEvent.card_id) == first, col(CardEvent.to_state) == CardState.INVALIDATED
            )
        )
    assert reason == CardReason.CORRECTION
    assert await w.thread_next(first) == ()
    [new] = await cards_by(w, state=CardState.PENDING)
    assert new.action == CardAction.ENTRY and new.card_id != first
    assert new.provider_position_id == opened_position(a1)  # the same position, corrected
    assert new.proposal["qty"] == 20 and new.proposal["limit_price"] == "0.49"
    assert await w.thread_next(new.card_id) == ("await_approval",)  # re-approval required
    pos = await position(w, a1)
    assert pos.strike == Decimal("360") and pos.is_corrected
    assert await w.orders() == []


async def test_correction_after_trade_alerts_only_and_lot_keeps_its_contract(w: World) -> None:
    a1, first = await w.entry()
    await w.decide(first, "reject")  # the user traded it by other means: a 4-lot exists
    async with in_transaction(w.h.factory, None) as db:
        await ledger.record_lot(
            db,
            apply_fill(None, "buy", 4, Decimal("0.74")),
            instrument=GOOGL,
            provider_position_id=opened_position(a1),
            provider_id=PROVIDER_A,
        )
    w.broker._position(GOOGL, 4, Decimal("0.74"))
    fix = await w.h.message(
        "correction: strike is 360",
        [
            ParsedSignal(
                msg_type="correction",
                fields=SignalFields(
                    strike=lit(Decimal("360"), "360"),
                    correction_target_message_id=a1.key.message_id,
                ),
                link_hint=LinkHint(target=None),
            )
        ],
        at=T0 + timedelta(seconds=30),
        author=PROVIDER_A,
    )
    await w.h.process(fix, processor=w.processor)

    [alert] = await alerts(w, AlertKind.LATE_CORRECTION)
    assert alert.details["original_text"] == "Bought GOOGL 357.5C 10/2 @ 0.71"
    assert alert.details["correction_text"] == "correction: strike is 360"
    assert alert.details["lot_pnl"] is not None
    assert await cards_by(w, state=CardState.PENDING) == []  # no action proposed
    assert (await position(w, a1)).strike == Decimal("360")

    trim = await w.h.message(
        "take half",
        [follow_up("trim", opened_position(a1), exit_fraction="half")],
        at=T0 + timedelta(seconds=40),
        author=PROVIDER_A,
    )
    await w.h.process(trim, processor=w.processor)
    [exit_card] = await cards_by(w, action=CardAction.TRIM)
    await w.decide(exit_card.card_id)
    [order] = await w.orders()
    assert order.broker_symbol == GOOGL.occ_symbol() != GOOGL_360.occ_symbol()
    assert order.qty == 2 and order.side == "sell"


async def test_delete_with_pending_card_invalidates_and_alerts(w: World) -> None:
    a1, first = await w.entry()
    gone = await w.h.delete(a1, at=T0 + timedelta(seconds=15))
    await w.h.process(gone, processor=w.processor)
    card = await w.card(first)
    assert card.state == CardState.INVALIDATED
    assert await w.thread_next(first) == ()
    [alert] = await alerts(w, AlertKind.MESSAGE_DELETED)
    assert alert.message_id == a1.key.message_id
    assert (await position(w, a1)).status != "closed"  # ledger unchanged


async def test_orders_table_untouched_by_edits(w: World) -> None:
    a1, _ = await w.entry()
    edit = await w.h.edit(
        a1,
        "Bought GOOGL 357.5C 10/2 @ 0.71 (typo fixed)",
        [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")],
        at=T0 + timedelta(seconds=20),
    )
    await w.h.process(edit, processor=w.processor)
    [new] = await cards_by(w, state=CardState.PENDING)
    assert new.provider_position_id == opened_position(a1)
    assert not (await position(w, a1)).is_corrected  # same contract: nothing to correct
    async with w.h.factory() as db:
        assert (await db.scalars(select(Order))).all() == []
