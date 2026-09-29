"""F044 / C49: a system-held underlying stop fires the C25 exit exactly once (OD-32: last)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.cards.models import Card, CardAction
from app.execution.models import AutoReason, Order, OrderKind, Stop, StopStatus
from app.execution.underlying_stops import UnderlyingStopWatcher, Watched, crossed
from app.ledger.models import Lot
from tests.integration.graph.conftest import World
from tests.integration.pipeline.harness import lit, option_entry
from tests.messages import PROVIDER_A

pytestmark = pytest.mark.graph


def watcher(w: World, **kw: float) -> UnderlyingStopWatcher:
    exits = w.approvals._executor.exit
    assert exits is not None
    return UnderlyingStopWatcher(
        w.h.factory, marketdata=w.h.md, exits=exits, alerts=w.h.alerts, **kw
    )


async def lot_with_underlying_stop(w: World) -> None:
    sig = option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.71")
    fields = sig.fields.model_copy(
        update={"stop_level": lit(Decimal("354"), "354"), "stop_basis": "underlying"}
    )
    env = await w.h.message(
        "Bought GOOGL 357.5C 10/2 @ 0.71 stop if GOOGL below 354",
        [sig.model_copy(update={"fields": fields})],
        author=PROVIDER_A,
    )
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None and card.proposal["stop"]["held_by"] == "system", card.proposal
    await w.decide(card.card_id, qty=2)
    await w.consume()


def test_calls_fire_on_the_way_down_puts_on_the_way_up() -> None:
    call = Watched(uuid.uuid4(), uuid.uuid4(), "SPY", Decimal("769"), fires_on_rise=False)
    put = Watched(uuid.uuid4(), uuid.uuid4(), "SPY", Decimal("771"), fires_on_rise=True)
    assert not crossed(call, Decimal("770.2")) and crossed(call, Decimal("769.0"))
    assert not crossed(put, Decimal("770.9")) and crossed(put, Decimal("771"))


async def test_crossing_fires_the_exit_once(w: World) -> None:
    await lot_with_underlying_stop(w)
    async with w.h.factory() as db:
        [row] = (await db.scalars(select(Stop))).all()
        assert (
            await db.scalars(select(Order).where(col(Order.kind) == OrderKind.STOP))
        ).all() == []
    assert row.held_by == "system" and row.status == StopStatus.WORKING and row.qty == 2

    s = watcher(w)
    watched = await s.load()
    assert await s.on_price("GOOGL", Decimal("354.40"), watched) == []
    assert await s.on_price("GOOGL", Decimal("354.00"), watched) == [row.stop_id]
    assert await s.on_price("GOOGL", Decimal("353.00"), watched) == []  # exactly once
    await w.exits_done()

    async with w.h.factory() as db:
        exits = (await db.scalars(select(Order).where(col(Order.kind) == OrderKind.EXIT))).all()
        lot = await db.scalar(select(Lot))
        [alert] = (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.UNDERLYING_STOP))
        ).all()
    assert exits and all(
        o.auto_reason in (AutoReason.UNDERLYING_STOP, AutoReason.REPRICE) for o in exits
    )
    assert exits[0].auto_reason == AutoReason.UNDERLYING_STOP and exits[0].approval_id is None
    assert lot is not None and lot.qty == 0
    assert Decimal(alert.details["level"]) == Decimal("354")
    assert await s.load() == []


async def test_silent_stream_alerts_once(w: World) -> None:
    await lot_with_underlying_stop(w)
    s = watcher(w, gap_s=0.05, reload_s=0.3)
    await s._watch(await s.load())
    async with w.h.factory() as db:
        alerts = (
            await db.scalars(select(Alert).where(col(Alert.kind) == AlertKind.MARKET_DATA))
        ).all()
    assert len(alerts) == 1 and "unprotected" in alerts[0].message
