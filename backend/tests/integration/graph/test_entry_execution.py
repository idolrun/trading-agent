"""F041 / C22: the approved entry is one limit at the approved limit; after M seconds the
unfilled remainder is cancelled and partial fills stay in the lot."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.execution.models import OrderStatus
from app.ledger.models import Lot
from app.timers.models import TimerKind
from app.timers.service import TimerService
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL

pytestmark = pytest.mark.graph
NOT_MARKETABLE = (Decimal("0.73"), Decimal("0.745"))  # inside the C22 band, above the 0.74 limit


def timers(w: World) -> TimerService:
    service = TimerService(w.h.factory, alerts=w.h.alerts, clock=w.h.clock)
    entry = w.approvals._executor.entry
    assert entry is not None
    service.register(TimerKind.ENTRY_CANCEL, entry.on_entry_cancel)
    return service


async def lots(w: World) -> list[Lot]:
    async with w.h.factory() as db:
        return list((await db.scalars(select(Lot))).all())


async def test_marketable_entry_fills_in_full(w: World) -> None:
    _, card_id = await w.entry()
    await w.decide(card_id)
    await w.consume()
    [order] = await w.orders()
    assert order.status == OrderStatus.FILLED and order.limit_price == Decimal("0.74")
    [lot] = await lots(w)
    assert lot.qty == 13
    w.h.clock.advance(timedelta(minutes=5))
    assert await timers(w).run_due() == 0  # nothing left to cancel: no timer was scheduled


async def test_the_broker_gets_exactly_the_quantity_the_user_approved(w: World) -> None:
    """C105: whatever the user types is the order; the card never held a number of its own."""
    _, card_id = await w.entry()
    assert (await w.card(card_id)).proposal["qty"] is None
    await w.decide(card_id, qty=7)
    [order] = await w.orders()
    assert order.qty == 7 and order.limit_price == Decimal("0.74")
    approved = await w.approvals._load(card_id)
    assert approved.approval is not None and approved.approval.approved_proposal["qty"] == 7
    await w.consume()
    [lot] = await lots(w)
    assert lot.qty == 7


async def test_a_missing_or_changed_quantity_places_no_order(w: World) -> None:
    from app.cards.approval_service import DecisionConflict, DecisionInvalid

    _, card_id = await w.entry()
    with pytest.raises(DecisionInvalid, match="qty required"):
        await w.decide(card_id, bare=True)
    await w.decide(card_id, "edit", 1, price=Decimal("0.75"))  # an edit: version 2, still pending
    with pytest.raises(DecisionConflict):  # an approval of the old version is not this card
        await w.decide(card_id, version=1, qty=3)
    assert await w.orders() == []
    await w.decide(card_id, version=2, qty=3)
    with pytest.raises(DecisionConflict):  # approved: the quantity cannot be changed again
        await w.decide(card_id, version=2, qty=9)
    [order] = await w.orders()
    assert order.qty == 3


async def test_unfilled_entry_is_cancelled_after_m_seconds(w: World) -> None:
    _, card_id = await w.entry()
    w.h.md.set_quote(GOOGL, *NOT_MARKETABLE)
    await w.decide(card_id)
    [order] = await w.orders()
    assert order.status == OrderStatus.WORKING
    service = timers(w)
    w.h.clock.advance(timedelta(seconds=119))
    assert await service.run_due() == 0
    w.h.clock.advance(timedelta(seconds=1))
    assert await service.run_due() == 1
    [order] = await w.orders()
    assert order.status == OrderStatus.CANCELLED and order.filled_qty == 0
    assert await lots(w) == []


async def test_partial_fill_keeps_the_partial_and_cancels_the_rest(w: World) -> None:
    _, card_id = await w.entry()
    w.h.md.set_quote(GOOGL, *NOT_MARKETABLE)
    await w.decide(card_id)
    [order] = await w.orders()
    assert order.broker_order_id is not None
    w.broker.fill(order.broker_order_id, qty=1, price=Decimal("0.74"))
    await w.consume()
    w.h.clock.advance(timedelta(seconds=120))
    await timers(w).run_due()
    [order] = await w.orders()
    assert order.status == OrderStatus.CANCELLED and order.filled_qty == 1
    [lot] = await lots(w)
    assert lot.qty == 1
    async with w.h.factory() as db:
        [alert] = (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.ENTRY_PARTIAL))
        ).all()
    assert alert.details == {"filled": 1, "qty": 13}
