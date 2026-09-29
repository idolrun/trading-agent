"""F047 / AC-3: a manual exit goes through the same path as a signal exit, so a stop that fires
after it can never oversell (FR-20). The fixtures live with the integration tests."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlmodel import col

from app.cards.models import Card, CardSource, CardState
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Fill, Order, OrderKind, OrderStatus, Side
from app.ledger.models import Lot, LotStatus
from app.manual.service import ManualService
from app.reconcile.service import Reconciler
from app.system.flags import FlagService
from tests.integration.conftest import _clean_tables, db_session, owner_engine  # noqa: F401
from tests.integration.graph.conftest import World, factory, w  # noqa: F401
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.graph.test_brackets import two_lot, working

pytestmark = pytest.mark.graph


def manual_service(w: World) -> ManualService:  # noqa: F811
    brackets = w.approvals._executor.brackets
    assert brackets is not None
    flags = FlagService(w.h.factory, w.h.alerts)
    return ManualService(
        w.h.factory,
        approvals=w.approvals,
        brackets=brackets,
        reconciler=Reconciler(
            w.h.factory,
            broker=w.broker,
            consumer=BrokerEventConsumer(w.h.factory, [w.broker]),
            flags=flags,
            alerts=w.h.alerts,
            config=w.h.config,
            clock=w.h.clock,
        ),
        flags=flags,
        marketdata=w.h.md,
        clock=w.h.clock,
    )


async def test_ac3_stop_firing_after_a_manual_exit_finds_it_reduced(w: World) -> None:  # noqa: F811
    await two_lot(w)  # lot of 2 with a stop for 2 and two take-profits
    async with w.h.factory() as db:
        lot = await db.scalar(select(Lot))
    assert lot is not None and lot.qty == 2
    w.h.md.set_quote(GOOGL, Decimal("0.40"), Decimal("0.71"))  # the exit rests at the mid

    result = await manual_service(w).exit(lot.lot_id, 1, "x-1", w.session_id)
    assert result.state == CardState.SUBMITTED
    async with w.h.factory() as db:
        card = await db.get(Card, result.card_id)
    assert card is not None and card.source == CardSource.MANUAL

    legs = await working(w)
    [stop] = legs[OrderKind.STOP]
    assert stop.qty == 1  # reduced first: stop + exit <= lot (FR-20(3))
    assert sum(o.qty for o in legs[OrderKind.EXIT]) == 1

    assert stop.broker_order_id is not None
    w.broker.fill(stop.broker_order_id, price=Decimal("0.50"))  # the stop fires after the exit
    await w.consume()
    w.h.md.set_quote(GOOGL, Decimal("0.56"), Decimal("0.71"))  # the next re-price is marketable
    await w.exits_done()

    async with w.h.factory() as db:
        lot = await db.scalar(select(Lot))
        sold = await db.scalar(
            select(func.sum(col(Fill.qty)))
            .join(Order, col(Order.order_id) == col(Fill.order_id))
            .where(col(Order.side) == Side.SELL)
        )
        statuses = {
            o.status for o in (await db.scalars(select(Order))).all() if o.side == Side.SELL
        }
    assert lot is not None and (lot.qty, lot.status) == (0, LotStatus.CLOSED)
    assert sold == 2  # the stop's 1 and the exit's 1: never more than the lot held
    assert OrderStatus.WORKING not in statuses
    assert [p for p in await w.broker.positions() if p.qty != 0] == []  # never short
