"""F028: submit_order against real PG + FakeBroker (paper) / SimulatedBroker (shadow)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import col

from app.alerts.models import Alert
from app.alerts.service import AlertService
from app.broker.fake import Behavior, FakeBroker
from app.broker.simulated import SimulatedBroker
from app.cards import repo as cards
from app.cards.models import Approval, Card, CardAction, CardSource, CardState, CardType
from app.db import create_session_factory, in_transaction
from app.execution.audit import ac2_violations
from app.execution.intent import Approved, OrderIntent, client_order_id
from app.execution.models import Order, OrderEvent
from app.execution.submit import EntriesBlocked, InvariantViolation, OrderService
from app.instruments import OptionContract
from app.ledger import repo as ledger
from app.ledger.lots import apply_fill
from app.ledger.positions import apply_to_position
from app.marketdata.fake import FakeMarketData
from app.markettime import FixedClock
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig, TradingMode
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName
from app.validate.normalize import NormalizedSignal
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import Factory
from tests.integration.schema.helpers import add_session, add_signal

NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


class World:
    """Provider A's position + the user's 2-lot, an approved card, a broker holding 2."""

    def __init__(self, factory: Factory, mode: TradingMode = TradingMode.PAPER) -> None:
        self.factory = factory
        self.clock = FixedClock(NOW)
        self.broker = FakeBroker(self.clock)
        self.md = FakeMarketData(self.clock)
        self.simulated = SimulatedBroker(self.md, self.clock)
        self.alerts = AlertService(factory)
        self.flags = FlagService(factory, self.alerts)
        self.mode = mode

    async def setup(
        self, db_session: AsyncSession, provider_id: int = 7, message_id: int = 1001
    ) -> None:
        self.config = ConfigService(self.factory)
        await self.config.update(
            RuntimeConfig(trading_mode=self.mode), expected_version=0, actor=Actor.CLI, reason="t"
        )
        self.service = OrderService(
            self.factory,
            broker=self.broker,
            simulated=self.simulated,
            flags=self.flags,
            alerts=self.alerts,
            config=self.config,
            clock=self.clock,
        )
        sig = await add_signal(db_session, provider_id=provider_id, message_id=message_id)
        entry = NormalizedSignal(
            msg_type="entry",
            instrument="option",
            underlying="GOOGL",
            expiry=GOOGL.expiry,
            strike=GOOGL.strike,
            right="call",
        )
        session_id = await add_session(db_session)
        async with in_transaction(self.factory, None) as db:
            update = apply_to_position(None, entry)
            assert update is not None
            position = await ledger.record(
                db, provider_id=provider_id, row=None, update=update, signal_id=sig.signal_id
            )
            lot = await ledger.record_lot(
                db,
                apply_fill(None, "buy", 2, Decimal("1")),
                instrument=GOOGL,
                provider_position_id=position.provider_position_id,
                provider_id=provider_id,
            )
            key = f"{sig.signal_id}:exit"
            card = Card(
                card_id=cards.card_id_for(key),
                dedupe_key=key,
                source=CardSource.SIGNAL,
                signal_id=sig.signal_id,
                action=CardAction.EXIT,
                card_type=CardType.NORMAL,
                state=CardState.PENDING,
                provider_id=provider_id,
                provider_position_id=position.provider_position_id,
                lot_id=lot.lot_id,
                proposal={},
                posted_at=NOW,
            )
            await cards.insert(db, card)
            approval = Approval(
                card_id=card.card_id,
                decision="approve",
                approved_proposal={},
                card_version=1,
                session_id=session_id,
            )
            db.add(approval)
            await db.flush()
        self.position_id, self.lot_id = position.provider_position_id, lot.lot_id
        self.card_id, self.approval = card.card_id, Approved(approval.approval_id)
        for broker in (self.broker, self.simulated):  # the broker holds the user's 2 contracts
            broker._position(GOOGL, 2, Decimal("1"))

    def sell(self, qty: int = 1, kind: str = "exit", **kw: Any) -> OrderIntent:
        return OrderIntent.model_validate(
            {
                "card_id": self.card_id,
                "kind": kind,
                "side": "sell",
                "instrument": GOOGL,
                "qty": qty,
                "order_type": "limit",
                "limit_price": Decimal("0.70"),
                "tif": "day",
                "provider_position_id": self.position_id,
                "lot_id": self.lot_id,
                **kw,
            }
        )


async def orders(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(Order)) or 0)


async def test_happy_path_and_duplicate_is_one_order(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = World(factory)
    await w.setup(db_session)
    first = await w.service.submit_order(w.sell(), w.approval)
    again = await w.service.submit_order(w.sell(), w.approval)  # re-executed graph node (AC-7)
    assert first.order_id == again.order_id and first.status == "working"
    assert [c for c, _ in w.broker.calls].count("submit") == 1
    assert await orders(db_session) == 1
    statuses = (await db_session.scalars(select(OrderEvent.status).order_by(OrderEvent.id))).all()
    assert statuses == ["intent", "working"]  # intent committed before the broker call
    assert await ac2_violations(db_session) == []


async def test_invariants_block_without_a_row(factory: Factory, db_session: AsyncSession) -> None:
    w = World(factory)
    await w.setup(db_session)
    with pytest.raises(InvariantViolation) as exc:
        await w.service.submit_order(w.sell(3), w.approval)
    assert "sell_exceeds_lot" in exc.value.reasons
    with pytest.raises(InvariantViolation) as exc:  # AC-3: B's position against A's lot
        await w.service.submit_order(
            w.sell(1, provider_position_id=uuid.uuid4(), action_seq=2), w.approval
        )
    assert exc.value.reasons == ["other_providers_lot"]
    await w.service.submit_order(
        w.sell(2, "stop", order_type="stop", stop_price=Decimal("0.5"), action_seq=3), w.approval
    )
    with pytest.raises(InvariantViolation) as exc:  # protective legs over-commit the lot
        await w.service.submit_order(w.sell(1, "tp", action_seq=4), w.approval)
    assert exc.value.reasons == ["protective_exceeds_lot"]
    assert await orders(db_session) == 1  # only the stop
    blocks = (
        await db_session.scalars(select(Alert.kind).where(col(Alert.kind) == "invariant_block"))
    ).all()
    assert len(blocks) == 3


async def test_timeout_then_exists_is_adopted(factory: Factory, db_session: AsyncSession) -> None:
    w = World(factory)
    await w.setup(db_session)
    intent = w.sell()
    w.broker.script(client_order_id(intent, w.approval), Behavior.TIMEOUT_THEN_EXISTS)
    order = await w.service.submit_order(intent, w.approval)
    assert order.status == "working" and order.broker_order_id is not None
    assert [c for c, _ in w.broker.calls] == ["submit", "get_by_client_id"]  # never resubmitted


async def test_timeout_then_missing_voids_the_intent(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = World(factory)
    await w.setup(db_session)
    intent = w.sell()
    w.broker.script(client_order_id(intent, w.approval), Behavior.TIMEOUT_THEN_MISSING)
    order = await w.service.submit_order(intent, w.approval)
    assert order.status == "rejected"
    assert [c for c, _ in w.broker.calls] == ["submit", "get_by_client_id"]
    statuses = (await db_session.scalars(select(OrderEvent.status).order_by(OrderEvent.id))).all()
    assert statuses == ["intent", "unknown", "rejected"]
    assert (await db_session.scalars(select(Alert.kind))).all() == ["order_failed"]


async def test_kill_switch_refuses_entries_not_exits(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = World(factory)
    await w.setup(db_session)
    await w.flags.set(FlagName.KILL_SWITCH, True, "test", Actor.CLI)
    buy = OrderIntent(
        card_id=w.card_id,
        action_seq=9,
        kind="entry",
        side="buy",
        instrument=GOOGL,
        qty=1,
        order_type="limit",
        limit_price=Decimal("0.75"),
        tif="day",
    )
    with pytest.raises(EntriesBlocked):
        await w.service.submit_order(buy, w.approval)
    order = await w.service.submit_order(w.sell(), w.approval)
    assert order.status == "working"


async def test_shadow_mode_never_calls_the_real_broker(
    factory: Factory, db_session: AsyncSession
) -> None:
    w = World(factory, TradingMode.SHADOW)
    await w.setup(db_session)
    w.md.set_quote(GOOGL, Decimal("0.70"), Decimal("0.71"))
    order = await w.service.submit_order(w.sell(2), w.approval)
    assert (order.mode, order.status, order.filled_qty) == ("shadow", "filled", 2)  # OD-28
    assert w.broker.calls == []
    buy = OrderIntent(
        card_id=w.card_id,
        action_seq=1,
        kind="entry",
        side="buy",
        instrument=GOOGL,
        qty=1,
        order_type="limit",
        limit_price=Decimal("0.60"),
        tif="day",
    )
    resting = await w.service.submit_order(buy, w.approval)
    assert resting.status == "working"  # 0.60 < ask 0.71: not marketable, rests
    assert w.broker.calls == [] and await ac2_violations(db_session) == []
