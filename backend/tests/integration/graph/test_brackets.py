"""F042 / AC-16: the approved bracket for what filled; linkage on fills; stop cards."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.cards.models import Card, CardAction
from app.execution.models import Order, OrderKind, OrderStatus, OrderType, Stop, StopStatus
from app.inbox.envelope import Envelope
from app.interpret.signal import ParsedSignal
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.pipeline.harness import T0, follow_up, lit, opened_position, option_entry
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph
TEXT = "Bought GOOGL 357.5C 10/2 @ 0.71 SL 0.50 TP 1.00 1.50 2.00"


def bracketed_entry() -> ParsedSignal:
    sig = option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")
    fields = sig.fields.model_copy(
        update={
            "stop_level": lit(Decimal("0.50"), "0.50"),
            "tp_levels": [lit(Decimal(v), v) for v in ("1.00", "1.50", "2.00")],
        }
    )
    return sig.model_copy(update={"fields": fields})


async def two_lot(w: World) -> Envelope:
    """AC-16: 2 contracts, 3 TPs, stop."""
    env = await w.h.message(TEXT, [bracketed_entry()], author=PROVIDER_A)
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None and card.proposal["stop"]["level"] == "0.50", card.proposal
    await w.decide(card.card_id, qty=2)
    await w.consume()
    return env


async def working(w: World) -> dict[str, list[Order]]:
    async with w.h.factory() as db:
        rows = (
            await db.scalars(
                select(Order)
                .where(col(Order.status) == OrderStatus.WORKING)
                .order_by(col(Order.created_at))
            )
        ).all()
    out: dict[str, list[Order]] = {}
    for o in rows:
        out.setdefault(o.kind, []).append(o)
    return out


async def test_ac16_bracket_for_the_filled_lot(w: World) -> None:
    await two_lot(w)
    legs = await working(w)
    [stop] = legs[OrderKind.STOP]
    assert (stop.qty, stop.stop_price, stop.order_type) == (2, Decimal("0.50"), OrderType.STOP)
    assert [(o.qty, o.limit_price) for o in legs[OrderKind.TP]] == [
        (1, Decimal("1.00")),
        (1, Decimal("1.50")),
    ]
    assert len({o.oco_group_id for o in [stop, *legs[OrderKind.TP]]}) == 1
    assert all(o.approval_id is not None for o in [stop, *legs[OrderKind.TP]])  # AC-2


async def test_tp_fill_shrinks_the_stop_and_stop_fill_cancels_the_tps(w: World) -> None:
    await two_lot(w)
    legs = await working(w)
    tp1 = legs[OrderKind.TP][0]
    assert tp1.broker_order_id is not None
    w.broker.fill(tp1.broker_order_id, price=Decimal("1.00"))
    await w.consume()
    legs = await working(w)
    [stop] = legs[OrderKind.STOP]
    assert stop.qty == 1 and stop.parent_order_id is not None  # resized under the approval
    assert [(o.qty, o.limit_price) for o in legs[OrderKind.TP]] == [(1, Decimal("1.50"))]

    assert stop.broker_order_id is not None
    w.broker.fill(stop.broker_order_id, price=Decimal("0.50"))
    await w.consume()
    assert await working(w) == {}
    async with w.h.factory() as db:
        statuses = {r.status for r in (await db.scalars(select(Stop))).all()}
    assert StopStatus.WORKING not in statuses and StopStatus.FIRED in statuses


async def test_stop_card_moves_the_stop(w: World) -> None:
    env = await two_lot(w)
    old = (await working(w))[OrderKind.STOP][0]
    msg = await w.h.message(
        "raise stop to 0.60",
        [follow_up("stop_set", opened_position(env), stop_level=lit(Decimal("0.60"), "0.60"))],
        at=T0 + timedelta(seconds=20),
        author=PROVIDER_A,
    )
    await w.h.process(msg, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.STOP))
    assert card is not None
    await w.decide(card.card_id)
    [new] = (await working(w))[OrderKind.STOP]
    assert (
        new.stop_price == Decimal("0.60") and new.qty == 2 and new.parent_order_id == old.order_id
    )
    assert len((await working(w))[OrderKind.TP]) == 2  # TPs untouched


async def test_breakeven_choice_sets_the_level(w: World) -> None:
    env = await two_lot(w)
    msg = await w.h.message(
        "stop to breakeven",
        [follow_up("stop_set", opened_position(env), breakeven_stop=True)],
        at=T0 + timedelta(seconds=20),
        author=PROVIDER_A,
    )
    await w.h.process(msg, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.STOP))
    assert card is not None
    await w.decide(card.card_id, "edit", breakeven_choice="provider_entry")
    await w.decide(card.card_id, version=2)
    [stop] = (await working(w))[OrderKind.STOP]
    assert stop.stop_price == Decimal("0.71")


async def test_partial_entry_fill_gets_a_bracket_for_what_filled(w: World) -> None:
    """F041 step 4: partial 1 of 3 -> stop 1 and TP1 only (C83 on the filled qty)."""
    env = await w.h.message(TEXT, [bracketed_entry()], author=PROVIDER_A)
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None
    w.h.md.set_quote(GOOGL, Decimal("0.73"), Decimal("0.745"))  # rests: above the 0.74 limit
    await w.decide(card.card_id, qty=3)
    [entry] = (await working(w))[OrderKind.ENTRY]
    assert entry.broker_order_id is not None
    w.broker.fill(entry.broker_order_id, qty=1, price=Decimal("0.74"))
    await w.consume()
    legs = await working(w)
    assert [o.qty for o in legs[OrderKind.STOP]] == [1]
    assert [(o.qty, o.limit_price) for o in legs[OrderKind.TP]] == [(1, Decimal("1.00"))]
