"""Shadow lifecycle (PRD 7.1-7.4, FR-16, FR-20, AC-2, AC-3, AC-15, AC-16): an approved card goes
card -> order intent -> simulated order -> fill -> lot -> bracket -> trim -> exit -> closed lot,
and the broker position, the lot and the provider position agree after every step.

Reconciliation is skipped in shadow mode (OD-33), so these worlds run in PAPER mode over the
`SimulatedBroker`: the same code paths, no real broker.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: F401
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.alerts.models import Alert, AlertKind
from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import DecisionConflict, DecisionInvalid
from app.cards.models import (
    Approval,
    Card,
    CardAction,
    CardEvent,
    CardState,
    CardType,
)
from app.execution.audit import ac2_violations
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Fill, Order, OrderEvent, OrderKind, OrderStatus
from app.graph.checkpointer import open_pool, saver_and_store
from app.inbox.envelope import Envelope
from app.instruments import broker_symbol
from app.interpret.signal import ParsedSignal
from app.ledger.models import Lot, LotEvent, LotStatus, PositionStatus, ProviderPosition
from app.main import libpq_dsn
from app.reconcile.service import Mismatch, Reconciler
from app.runtime_config.schema import TradingMode
from app.system.flags import FlagService
from app.system.models import FlagName
from tests.integration.conftest import _url
from tests.integration.graph.conftest import World
from tests.integration.graph.test_approval_graph import GOOGL
from tests.integration.graph.test_brackets import working
from tests.integration.pipeline.harness import (
    T0,
    Factory,
    Harness,
    follow_up,
    lit,
    opened_position,
    option_entry,
)
from tests.integration.schema.helpers import add_session
from tests.messages import PROVIDER_A, PROVIDER_B

pytestmark = pytest.mark.graph
OCC = broker_symbol(GOOGL)
TEXT = "Bought GOOGL 357.5C 10/2 @ 1.67 SL 1.20 TP 2.00 2.50"


@pytest.fixture
async def lw(factory: Factory, tmp_path: Path, db_session: AsyncSession) -> AsyncIterator[World]:
    """PAPER mode over a SimulatedBroker (marketable orders fill at the quote)."""
    pool = open_pool(libpq_dsn(_url("test_database_url")))
    await pool.open()
    try:
        saver, _ = saver_and_store(pool)
        h = await Harness.create(factory, tmp_path, trading_mode=TradingMode.PAPER)
        h.md.set_quote(GOOGL, Decimal("1.70"), Decimal("1.75"))
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
        assert exit_policy is not None
        exit_policy.sleep = world.elapse
        yield world
        for task in list(exit_policy.loops.values()):
            task.cancel()
    finally:
        await pool.close()


def reconciler(w: World) -> Reconciler:
    return Reconciler(
        w.h.factory,
        broker=w.broker,
        consumer=BrokerEventConsumer(w.h.factory, [w.broker]),
        flags=FlagService(w.h.factory, w.h.alerts),
        alerts=w.h.alerts,
        config=w.h.config,
        clock=w.h.clock,
    )


def bracketed_entry() -> ParsedSignal:
    sig = option_entry("GOOGL", "357.5", "357.5C", "10/2", "1.67")
    fields = sig.fields.model_copy(
        update={
            "stop_level": lit(Decimal("1.20"), "1.20"),
            "tp_levels": [lit(Decimal(v), v) for v in ("2.00", "2.50")],
        }
    )
    return sig.model_copy(update={"fields": fields})


async def entry_card(
    w: World, author: int = PROVIDER_A, *, signal: ParsedSignal | None = None, at: Any = T0
) -> tuple[Envelope, Card]:
    env = await w.h.message(TEXT, [signal or bracketed_entry()], at=at, author=author)
    await w.h.process(env, processor=w.processor)
    async with w.h.factory() as db:
        card = await db.scalar(
            select(Card).where(
                col(Card.action) == CardAction.ENTRY, col(Card.provider_id) == author
            )
        )
    assert card is not None
    return env, card


async def follow(
    w: World,
    env: Envelope,
    msg_type: str,
    text: str,
    seconds: int,
    *,
    author: int = PROVIDER_A,
    **f: Any,
) -> Card:
    msg = await w.h.message(
        text,
        [follow_up(msg_type, opened_position(env), **f)],
        at=T0 + timedelta(seconds=seconds),
        author=author,
    )
    await w.h.process(msg, processor=w.processor)
    action = CardAction.EXIT if msg_type == "full_exit" else CardAction.TRIM
    async with w.h.factory() as db:
        card = await db.scalar(
            select(Card)
            .where(col(Card.action) == action, col(Card.state) == CardState.PENDING)
            .order_by(col(Card.created_at).desc())
        )
    assert card is not None
    return card


async def rows(w: World, model: type) -> list[Any]:
    async with w.h.factory() as db:
        return list((await db.scalars(select(model))).all())


async def lot(w: World) -> Lot:
    [found] = await rows(w, Lot)
    return found


async def position(w: World, env: Envelope) -> ProviderPosition:
    async with w.h.factory() as db:
        row = await db.get(ProviderPosition, opened_position(env))
    assert row is not None
    return row


async def agreed(w: World) -> None:
    """Reconciliation is clean and the broker holds exactly what the lot holds (FR-20, §11)."""
    report = await reconciler(w).run()
    assert report.clean and report.skipped is None, report
    [held] = await rows(w, Lot)
    positions = [p for p in await w.broker.positions() if p.qty]
    assert [(broker_symbol(p.instrument), p.qty) for p in positions] == (
        [(held.broker_symbol, held.qty)] if held.qty else []
    )
    async with w.h.factory() as db:
        assert await ac2_violations(db) == []  # every order has an approval or an allowed reason


async def legs(w: World) -> tuple[list[Order], list[Order]]:
    got = await working(w)
    return got.get(OrderKind.STOP, []), got.get(OrderKind.TP, [])


async def orders_of(w: World, card: Card) -> list[Order]:
    async with w.h.factory() as db:
        found = await db.scalars(select(Order).where(col(Order.card_id) == card.card_id))
        return list(found.all())


async def filled_exit(w: World, card: Card, qty: int) -> Order:
    """The approved exit's root order is the approval's; re-price children carry the fill."""
    orders = await orders_of(w, card)
    [root] = [o for o in orders if o.parent_order_id is None]
    assert (root.kind, root.side, root.qty) == (OrderKind.EXIT, "sell", qty)
    assert root.approval_id is not None
    filled = [o for o in orders if o.status == OrderStatus.FILLED]
    assert [o.qty for o in filled] == [qty]
    return filled[0]


async def card_trail(w: World, card_id: object) -> list[str]:
    async with w.h.factory() as db:
        found = await db.scalars(
            select(CardEvent.to_state)
            .where(col(CardEvent.card_id) == card_id)
            .order_by(col(CardEvent.id))
        )
        return [str(state) for state in found.all()]


async def test_approved_entry_fills_and_creates_the_lot(lw: World) -> None:
    w = lw
    env, card = await entry_card(w)
    assert card.card_type == CardType.NORMAL and card.state == CardState.PENDING
    assert await rows(w, Order) == []  # nothing before the approval (AC-2)
    await w.decide(card.card_id, qty=4)
    await w.consume()

    # approval -> one order intent -> one simulated order, one client id
    [approval] = await rows(w, Approval)
    assert approval.card_id == card.card_id and approval.decision == "approve"
    assert approval.edits == {"qty": 4}
    assert (await w.card(card.card_id)).state == CardState.SUBMITTED
    assert await card_trail(w, card.card_id) == ["pending", "approved", "submitted"]
    entries = [o for o in await rows(w, Order) if o.kind == OrderKind.ENTRY]
    [buy] = entries
    assert (buy.side, buy.qty, buy.limit_price, buy.status) == ("buy", 4, Decimal("1.75"), "filled")
    assert buy.approval_id == approval.approval_id and buy.card_id == card.card_id
    assert len({o.client_order_id for o in await rows(w, Order)}) == len(await rows(w, Order))

    # fill -> exactly one fill, one lot, linked to the provider position
    [fill] = await rows(w, Fill)
    assert (fill.order_id, fill.qty, fill.price) == (buy.order_id, 4, Decimal("1.75"))
    held = await lot(w)
    assert (held.qty, held.avg_price, held.status) == (4, Decimal("1.75"), LotStatus.OPEN)
    assert held.provider_position_id == opened_position(env) and held.provider_id == PROVIDER_A
    assert held.broker_symbol == OCC and buy.lot_id == held.lot_id
    assert [e.change for e in await rows(w, LotEvent)] == ["open"]
    history = [e.status for e in await rows(w, OrderEvent) if e.order_id == buy.order_id]
    assert history[0] == "intent" or "working" in history
    assert history[-1] == "filled"
    await agreed(w)


async def test_bracket_never_exceeds_the_entry_and_shares_one_oco(lw: World) -> None:
    w = lw
    _, card = await entry_card(w)
    await w.decide(card.card_id, qty=4)
    await w.consume()
    [stop], tps = await legs(w)
    assert stop.qty == 4 and stop.stop_price == Decimal("1.20")
    assert sum(o.qty for o in tps) <= 4 and [o.limit_price for o in tps] == [
        Decimal("2.00"),
        Decimal("2.50"),
    ]
    assert len({o.oco_group_id for o in [stop, *tps]}) == 1 and stop.oco_group_id is not None
    assert all(o.approval_id is not None for o in [stop, *tps])  # AC-2: under the entry approval
    await agreed(w)


async def test_full_lifecycle_entry_trim_exit_closed(lw: World) -> None:
    w = lw
    env, entry = await entry_card(w)
    await w.decide(entry.card_id, qty=4)
    await w.consume()
    await agreed(w)
    first = await lot(w)

    # "Take half off" -> a trim card on the same provider position, qty by the trim rule (4 / 2)
    trim = await follow(w, env, "trim", "Take half off", 30, exit_fraction="half")
    assert trim.lot_id == first.lot_id and trim.provider_position_id == opened_position(env)
    assert trim.proposal["qty"] == 2
    assert await orders_of(w, trim) == []  # approval required: nothing sent yet
    await w.decide(trim.card_id)
    await w.exits_done()
    await filled_exit(w, trim, 2)
    half = await lot(w)
    assert (half.lot_id, half.qty, half.avg_price) == (first.lot_id, 2, first.avg_price)
    assert half.status == LotStatus.OPEN and half.provider_position_id == opened_position(env)
    assert (await position(w, env)).status == PositionStatus.TRIMMED  # open, not closed
    [stop], tps = await legs(w)  # protection follows the remaining 2 (C35, never over the lot)
    assert stop.qty == 2 and sum(o.qty for o in tps) <= 2
    await agreed(w)

    # "Exit" -> sells the rest; protection is cancelled; lot and provider position close
    out = await follow(w, env, "full_exit", "all out", 60)
    assert out.proposal["qty"] == 2
    await w.decide(out.card_id)
    await w.exits_done()
    await filled_exit(w, out, 2)
    closed = await lot(w)
    assert (closed.qty, closed.status) == (0, LotStatus.CLOSED) and closed.closed_at is not None
    assert (await position(w, env)).status == PositionStatus.CLOSED
    assert await working(w) == {}  # no orphaned stop / TP
    assert [e.change for e in await rows(w, LotEvent)][-1] == "close"
    await agreed(w)

    # no second exit can sell the closed lot
    again = await w.h.message(
        "all out again",
        [follow_up("full_exit", opened_position(env))],
        at=T0 + timedelta(seconds=90),
        author=PROVIDER_A,
    )
    await w.h.process(again, processor=w.processor)
    sold = [o for o in await rows(w, Order) if o.kind == OrderKind.EXIT and o.status == "filled"]
    assert sum(o.qty for o in sold) == 4 and (await lot(w)).qty == 0  # 2 + 2, never a third
    assert all(f.qty > 0 for f in await rows(w, Fill))
    # every transition left an audit trail: approvals for entry, trim and exit
    assert {a.card_id for a in await rows(w, Approval)} == {
        entry.card_id,
        trim.card_id,
        out.card_id,
    }
    assert [await card_trail(w, c.card_id) for c in (entry, trim, out)] == [
        ["pending", "approved", "submitted"]
    ] * 3


async def test_duplicate_approval_does_not_duplicate_order(lw: World) -> None:
    w = lw
    _, card = await entry_card(w)
    await w.decide(card.card_id, qty=4)
    with pytest.raises(DecisionConflict):  # the second decision is refused
        await w.decide(card.card_id, qty=4)
    await w.consume()
    assert len([o for o in await rows(w, Order) if o.kind == OrderKind.ENTRY]) == 1
    assert len(await rows(w, Approval)) == 1 and len(await rows(w, Fill)) == 1
    assert (await lot(w)).qty == 4


async def test_duplicate_fill_does_not_double_the_lot(lw: World) -> None:
    w = lw
    _, card = await entry_card(w)
    await w.decide(card.card_id, qty=4)
    consumer = BrokerEventConsumer(w.h.factory, [w.broker])
    events = w.broker.pending_events()
    for event in [*events, *events]:  # the broker redelivers everything
        await consumer.handle(event)
    assert (await lot(w)).qty == 4 and len(await rows(w, Fill)) == 1
    await agreed(w)


async def test_one_contract_trim_requires_a_quantity(lw: World) -> None:
    w = lw
    env, card = await entry_card(w)
    await w.decide(card.card_id, qty=1)
    await w.consume()
    trim = await follow(w, env, "trim", "Take half off", 30, exit_fraction="half")
    assert trim.proposal["qty"] is None and "lot_too_small" in trim.proposal["labels"]  # AC-15
    assert await orders_of(w, trim) == []
    with pytest.raises(DecisionInvalid):  # approving with no quantity is refused
        await w.decide(trim.card_id)
    assert await orders_of(w, trim) == [] and (await lot(w)).qty == 1
    await w.decide(trim.card_id, qty=1)  # the user types it
    await w.exits_done()
    assert (await lot(w)).qty == 0


async def test_exit_cannot_exceed_the_lot(lw: World) -> None:
    w = lw
    env, card = await entry_card(w)
    await w.decide(card.card_id, qty=2)
    await w.consume()
    out = await follow(w, env, "full_exit", "all out", 30)
    with pytest.raises(DecisionInvalid):  # more than the lot holds
        await w.decide(out.card_id, qty=3)
    sold = sum(o.qty for o in await rows(w, Order) if o.side == "sell" and o.kind == "exit")
    assert sold == 0 and (await lot(w)).qty == 2
    await agreed(w)


async def test_cross_provider_exit_is_blocked(lw: World) -> None:
    w = lw
    env, card = await entry_card(w, PROVIDER_A)
    await w.decide(card.card_id, qty=2)
    await w.consume()
    other = await w.h.message(
        "all out",
        [follow_up("full_exit", opened_position(env))],  # B names A's position
        at=T0 + timedelta(seconds=30),
        author=PROVIDER_B,
    )
    await w.h.process(other, processor=w.processor)
    async with w.h.factory() as db:
        exits = (await db.scalars(select(Card).where(col(Card.action) == CardAction.EXIT))).all()
    assert [(c.card_type, c.lot_id) for c in exits] == [
        (CardType.REVIEW, None)
    ]  # B never gets A's lot
    assert [o for o in await rows(w, Order) if o.side == "sell" and o.kind == "exit"] == []
    held = await lot(w)
    assert held.qty == 2 and held.provider_id == PROVIDER_A
    await agreed(w)


async def test_reconciliation_blocks_entries_on_a_mismatch(lw: World) -> None:
    w = lw
    _, card = await entry_card(w)
    await w.decide(card.card_id, qty=4)
    await w.consume()
    rec = reconciler(w)
    assert (await rec.run()).clean

    w.broker.seed_position(GOOGL, 3, Decimal("1.75"))  # broker says 3, the lot says 4
    report = await rec.run()
    assert report.mismatches == (Mismatch(OCC, 3, 4),) and not report.clean
    assert await FlagService(w.h.factory, w.h.alerts).is_active(FlagName.ENTRIES_BLOCKED)
    async with w.h.factory() as db:
        kind = col(Alert.kind) == AlertKind.RECONCILE_MISMATCH
        assert await db.scalar(select(func.count()).select_from(Alert).where(kind)) == 1
    assert (await lot(w)).qty == 4  # never silently repaired

    _, blocked = await entry_card(w, PROVIDER_B, at=T0 + timedelta(seconds=40))
    assert blocked.state == CardState.DISABLED  # no new entry while the books disagree
