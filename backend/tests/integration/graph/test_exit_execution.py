"""F043 / AC-3: exits reduce protection first, start at the mid, re-price toward the bid down
to the floor, then restore protection; never an oversell."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.cards.models import Card, CardAction, CardState
from app.execution.models import AutoReason, Order, OrderKind, OrderStatus
from app.ledger.models import Lot, LotStatus
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.graph.test_brackets import two_lot, working
from tests.integration.pipeline.harness import T0, follow_up, opened_position
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph


async def approve_exit(w: World, env: object) -> Card:
    msg = await w.h.message(
        "all out",
        [follow_up("full_exit", opened_position(env))],  # type: ignore[arg-type]
        at=T0 + timedelta(seconds=30),
        author=PROVIDER_A,
    )
    await w.h.process(msg, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.EXIT))
    assert card is not None and card.proposal["qty"] == 2
    await w.decide(card.card_id)
    return card


async def exit_orders(w: World) -> list[Order]:
    async with w.h.factory() as db:
        rows = await db.scalars(
            select(Order).where(col(Order.kind) == OrderKind.EXIT).order_by(col(Order.created_at))
        )
        return list(rows.all())


async def lot(w: World) -> Lot:
    async with w.h.factory() as db:
        found = await db.scalar(select(Lot))
    assert found is not None
    return found


async def test_unfilled_exit_walks_to_the_floor_then_restores_the_stop(w: World) -> None:
    env = await two_lot(w)
    w.h.md.set_quote(GOOGL, Decimal("0.40"), Decimal("0.71"))  # mid 0.555, floor 0.45
    card = await approve_exit(w, env)
    assert (await w.card(card.card_id)).state == CardState.SUBMITTED
    await w.exits_done()

    root, *children = await exit_orders(w)
    assert root.limit_price == Decimal("0.56") and root.approval_id is not None
    prices = [o.limit_price for o in children]
    assert prices == sorted(prices, reverse=True) and prices[-1] == Decimal("0.45")
    assert all(
        o.auto_reason == AutoReason.REPRICE and o.parent_order_id == root.order_id for o in children
    )
    assert all(o.status == OrderStatus.CANCELLED for o in [root, *children])
    async with w.h.factory() as db:
        [alert] = (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.EXIT_FLOOR))
        ).all()
    assert alert.details["floor"] == "0.45"
    legs = await working(w)
    [stop] = legs[OrderKind.STOP]
    assert stop.qty == 2 and stop.auto_reason == AutoReason.STOP_RESTORE  # §9 pre-approved
    assert (await lot(w)).qty == 2


async def test_exit_fills_after_one_step_and_nothing_is_restored(w: World) -> None:
    env = await two_lot(w)
    w.h.md.set_quote(GOOGL, Decimal("0.71"), Decimal("0.72"))  # mid 0.715 -> 0.72, then 0.71 fills
    await approve_exit(w, env)
    await w.exits_done()
    _root, child = await exit_orders(w)
    assert child.status == OrderStatus.FILLED and child.limit_price == Decimal("0.71")
    closed = await lot(w)
    assert closed.qty == 0 and closed.status == LotStatus.CLOSED
    assert await working(w) == {}  # protection was cancelled before the exit, none restored
    [position] = await w.broker.positions() or [None]
    assert position is None or position.qty == 0  # never short


async def test_stop_fired_before_the_exit_prevents_an_oversell(w: World) -> None:
    env = await two_lot(w)
    [stop] = (await working(w))[OrderKind.STOP]
    assert stop.broker_order_id is not None
    w.broker.fill(stop.broker_order_id, price=Decimal("0.50"))  # the event is still in flight
    card = await approve_exit(w, env)
    assert await exit_orders(w) == []  # the fresh broker read shows 0: no sell (AC-3)
    assert (await w.card(card.card_id)).state == CardState.INVALIDATED
    await w.consume()
    assert (await lot(w)).qty == 0
    assert await working(w) == {}
