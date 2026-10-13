"""Application-level paper lifecycle: message -> Jev -> validation -> market data ->
broker account + working orders -> risk -> card -> approval -> revalidation -> `submit_order`
-> broker -> fill -> lot -> reconciliation.

PAPER mode over the `SimulatedBroker` (marketable orders fill at the quote) and the fake market
data: the same code paths as Alpaca paper, with no network. The opt-in live suite
(`tests/live/test_paper_app_flow.py`) runs the same path against the Alpaca paper account."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.broker.fake import Behavior, FakeBroker, Script
from app.broker.simulated import SimulatedBroker
from app.cards.approval_service import DecisionInvalid
from app.cards.models import Card, CardAction, CardEvent, CardReason, CardState, CardType
from app.execution.consumer import BrokerEventConsumer
from app.execution.models import Fill, Order, OrderKind, OrderStatus
from app.graph.checkpointer import open_pool, saver_and_store
from app.graph.processor import GraphProcessor
from app.instruments import OptionContract
from app.ledger.models import Lot
from app.llm.models import ModelCall
from app.main import libpq_dsn
from app.reconcile.service import Reconciler
from app.runtime_config.schema import TradingMode
from app.system.flags import FlagService
from tests.integration.conftest import _url
from tests.integration.graph.conftest import World
from tests.integration.graph.test_message_graph import JEV_ON, jev, jev_down
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
from tests.jev_helpers import c, example_answers
from tests.messages import PROVIDER_A, PROVIDER_B

pytestmark = pytest.mark.graph
GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
# "here" keeps it off the F070 fast path, so these tests exercise Jev
TEXT = "Bought GOOGL 357.5C 10/2 @ 0.71 here"


def answers(**override: Any) -> dict[str, Any]:
    """Confident Jev answers for TEXT: GOOGL (t1), 10/2 (e1), strike n1, entry price n2."""
    return example_answers(
        ticker=c("t1", 0.95),
        expiry=c("e1", 0.95),
        role_n1=c("strike", 0.97),
        role_n2=c("entry_price", 0.95),
        stop_basis=c("not_applicable", 0.95),
        **override,
    )


class RestingBroker(SimulatedBroker):
    """Accepts every order and leaves it working: an entry that has not filled yet."""

    async def submit(self, req: Any) -> Any:
        return await FakeBroker.submit(self, req)


@dataclass
class Paper:
    w: World

    async def processor(self, jev_client: Any) -> GraphProcessor:
        return self.w.h.processor(fixture=False, jev=jev_client, approvals=self.w.approvals)

    async def signal(
        self, processor: GraphProcessor, *, author: int = PROVIDER_A, at: Any = T0
    ) -> Card:
        env = await self.w.h.message(TEXT, None, at=at, author=author)
        await self.w.h.process(env, processor=processor)
        async with self.w.h.factory() as db:
            card = await db.scalar(
                select(Card)
                .where(col(Card.provider_id) == author)
                .order_by(col(Card.created_at).desc())
            )
        assert card is not None
        return card

    async def card(self, card_id: uuid.UUID) -> Card:
        return await self.w.card(card_id)

    async def events(self, card_id: uuid.UUID) -> list[CardEvent]:
        async with self.w.h.factory() as db:
            rows = await db.scalars(
                select(CardEvent)
                .where(col(CardEvent.card_id) == card_id)
                .order_by(col(CardEvent.id))
            )
            return list(rows.all())

    async def lots(self) -> list[Lot]:
        async with self.w.h.factory() as db:
            return list((await db.scalars(select(Lot))).all())

    async def reconcile(self) -> Any:
        return await Reconciler(
            self.w.h.factory,
            broker=self.w.broker,
            consumer=BrokerEventConsumer(self.w.h.factory, [self.w.broker]),
            flags=FlagService(self.w.h.factory, self.w.h.alerts),
            alerts=self.w.h.alerts,
            config=self.w.h.config,
            clock=self.w.h.clock,
        ).run()


def build(
    broker_cls: type[SimulatedBroker] = SimulatedBroker, **config: Any
) -> Callable[..., AsyncIterator[Paper]]:
    async def make(
        factory: Factory, tmp_path: Path, db_session: AsyncSession
    ) -> AsyncIterator[Paper]:
        pool = open_pool(libpq_dsn(_url("test_database_url")))
        await pool.open()
        try:
            saver, _ = saver_and_store(pool)
            h = await Harness.create(
                factory,
                tmp_path,
                trading_mode=TradingMode.PAPER,
                jev=JEV_ON,
                **config,
            )
            h.md.set_quote(GOOGL, Decimal("0.70"), Decimal("0.71"))
            broker = broker_cls(h.md, h.clock)
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
            yield Paper(world)
            for task in list(exit_policy.loops.values()):
                task.cancel()
        finally:
            await pool.close()

    return make


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[Factory]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import create_session_factory

    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def p(factory: Factory, tmp_path: Path, db_session: AsyncSession) -> AsyncIterator[Paper]:
    async for paper in build()(factory, tmp_path, db_session):
        yield paper


@pytest.fixture
async def resting(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> AsyncIterator[Paper]:
    # 1,500 total exposure: one 962 entry fits, two do not
    async for paper in build(RestingBroker, max_total_exposure_usd=Decimal("1500"))(
        factory, tmp_path, db_session
    ):
        yield paper


def jev_client(p: Paper, **override: Any) -> Any:
    return jev(p.w.h, **answers(**override))


# --- AI: Jev first, review when it cannot settle it (no LLM configured here) ----------------


async def test_jev_settles_the_signal(p: Paper) -> None:
    processor = await p.processor(jev_client(p))
    card = await p.signal(processor)
    assert (card.action, card.card_type, card.state) == (
        CardAction.ENTRY,
        CardType.NORMAL,
        CardState.PENDING,
    )
    assert card.proposal["qty"] is None and card.proposal["limit_price"] == "0.74"


async def test_an_unsure_jev_is_a_review(p: Paper) -> None:
    """F078: Jev answered but was unsure: a semantic doubt, so no LLM -> review."""
    unsure = jev_client(p, msg_type=c("entry", 0.6))
    card = await p.signal(await p.processor(unsure))
    assert card.card_type == CardType.REVIEW


async def test_jev_down_without_an_llm_gives_a_review_card_and_no_order(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_down(p.w.h)))
    assert card.card_type == CardType.REVIEW
    assert await p.w.orders() == []
    with pytest.raises(DecisionInvalid, match="could not be read"):  # nothing to approve
        await p.w.decide(card.card_id)
    assert (await p.card(card.card_id)).state == CardState.PENDING
    await p.w.decide(card.card_id, "reject")  # the user dismisses it
    assert (await p.card(card.card_id)).state == CardState.REJECTED
    assert await p.w.orders() == []
    assert [c for c, _ in p.w.broker.calls if c == "submit"] == []


# --- the whole path: approve -> revalidate -> broker -> fill -> lot -> reconcile ---------


async def test_approved_signal_becomes_an_order_a_fill_a_lot_and_reconciles(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_client(p)))
    await p.w.decide(card.card_id)

    assert (await p.card(card.card_id)).state == CardState.SUBMITTED
    [order] = await p.w.orders()
    assert (order.mode, order.kind, order.side, order.qty) == ("paper", OrderKind.ENTRY, "buy", 13)
    assert order.approval_id is not None and order.card_id == card.card_id
    assert [c for c, _ in p.w.broker.calls if c == "submit"] == ["submit"]

    await p.w.consume()
    [lot] = await p.lots()
    assert (lot.qty, lot.avg_price) == (13, Decimal("0.71"))
    async with p.w.h.factory() as db:
        assert (await db.get(Order, order.order_id)).status == OrderStatus.FILLED  # type: ignore[union-attr]
        assert await db.scalar(select(func.count()).select_from(Fill)) == 1

    report = await p.reconcile()
    assert report.clean and report.skipped is None
    assert report.account is not None and report.broker_positions == 1


async def test_preflight_shows_the_numbers_the_decision_rests_on(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_client(p)))
    pf = await p.w.approvals.preflight(card.card_id, qty=13)  # what the user typed
    assert pf.failure is None
    assert {c.name for c in pf.checks} >= {"hours", "validity", "quote_fresh", "price_band", "risk"}
    assert (pf.bid, pf.ask, pf.qty, pf.limit_price) == (
        Decimal("0.70"),
        Decimal("0.71"),
        13,
        Decimal("0.74"),
    )
    assert pf.est_cost == Decimal("962.00")
    assert pf.buying_power == Decimal(100_000)
    assert pf.projected_buying_power == Decimal(100_000) - Decimal("962.00")
    assert pf.projected_exposure == Decimal("962.00")
    p.w.broker.buying_power = Decimal(10_000)
    bigger = await p.w.approvals.preflight(card.card_id, qty=200)  # what the user typed
    assert bigger.est_cost == Decimal("14800.00")
    assert bigger.failure == "insufficient buying power"
    assert next(c for c in bigger.checks if c.name == "risk").status == "blocked"


# --- risk: account, working orders, freshness ----------------------------------------------


async def test_a_quantity_the_account_cannot_afford_is_refused_not_reduced(p: Paper) -> None:
    """C105: no quantity exists when the card is made, so the account cannot disable it then.
    The user's number is judged when typed; the refusal names the reason and changes nothing."""
    p.w.broker.buying_power = Decimal("500")  # 13 contracts cost 962
    card = await p.signal(await p.processor(jev_client(p)))
    assert card.state == CardState.PENDING and card.proposal["qty"] is None
    with pytest.raises(DecisionInvalid, match="insufficient buying power"):
        await p.w.decide(card.card_id, qty=13)
    after = await p.card(card.card_id)
    assert after.state == CardState.PENDING and after.proposal["qty"] is None
    assert await p.w.orders() == []
    await p.w.decide(card.card_id, qty=5)  # 5 contracts cost 370: the user chooses, and it fits
    assert [o.qty for o in await p.w.orders()] == [5]


async def test_buying_power_lost_between_card_and_approval_places_nothing(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_client(p)))
    p.w.broker.buying_power = Decimal("500")  # spent elsewhere in the meantime

    async def passed(*_: object) -> None:  # the approval-time check ran just before it went
        return None

    p.w.approvals._qty_gate = passed  # type: ignore[method-assign]
    await p.w.decide(card.card_id)  # revalidation still refuses
    assert (await p.card(card.card_id)).state == CardState.INVALIDATED
    assert await p.w.orders() == []
    last = (await p.events(card.card_id))[-1]
    assert last.detail["reason"] == "insufficient buying power"


async def test_an_unreadable_account_at_approval_places_nothing(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_client(p)))
    p.w.broker.account_error = RuntimeError("alpaca down")
    await p.w.decide(card.card_id)
    assert (await p.card(card.card_id)).state == CardState.INVALIDATED
    assert (await p.events(card.card_id))[-1].detail[
        "reason"
    ] == "risk unknown: account unavailable"
    assert await p.w.orders() == []


async def test_a_working_order_counts_against_the_exposure_limit(resting: Paper) -> None:
    p = resting
    processor = await p.processor(jev_client(p))
    first = await p.signal(processor, author=PROVIDER_A)
    again = await p.processor(jev_client(p))  # one scripted Jev answer per message
    second = await p.signal(again, author=PROVIDER_B, at=T0 + timedelta(seconds=1))
    assert first.state == second.state == CardState.PENDING  # each fits on its own: 962 <= 1500

    await p.w.decide(first.card_id)
    [working] = await p.w.orders()
    assert working.status in (OrderStatus.WORKING, OrderStatus.SUBMITTED)  # not filled: no lot yet
    assert await p.lots() == []

    with pytest.raises(DecisionInvalid, match="max exposure"):  # 962 working + 962 new > 1500
        await p.w.decide(second.card_id)
    assert (await p.card(second.card_id)).state == CardState.PENDING  # nothing resized
    assert len(await p.w.orders()) == 1  # not a second order


async def test_two_simultaneous_approvals_cannot_both_pass_the_exposure_limit(
    resting: Paper,
) -> None:
    """F076: approved at the same moment, each card's revalidation alone sees room for itself
    (962 <= 1500); the final risk check before submission is serialized per account, so the
    second sees the first's working order and is refused."""
    p = resting
    first = await p.signal(await p.processor(jev_client(p)), author=PROVIDER_A)
    second = await p.signal(
        await p.processor(jev_client(p)), author=PROVIDER_B, at=T0 + timedelta(seconds=1)
    )
    await asyncio.gather(p.w.decide(first.card_id), p.w.decide(second.card_id))
    assert len(await p.w.orders()) == 1
    states = sorted([(await p.card(first.card_id)).state, (await p.card(second.card_id)).state])
    assert states == sorted([CardState.SUBMITTED, CardState.INVALIDATED])


async def test_a_stale_quote_makes_a_review_card_and_never_an_order(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    async for p in build(quote_max_age_s=60)(factory, tmp_path, db_session):
        p.w.h.md.set_quote(GOOGL, Decimal("0.70"), Decimal("0.71"), at=T0 - timedelta(minutes=30))
        card = await p.signal(await p.processor(jev_client(p)))
        assert card.card_type == CardType.REVIEW
        assert "quote_stale" in card.proposal["review_reasons"]
        assert await p.w.orders() == []


async def test_a_quote_that_goes_stale_before_approval_places_nothing(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    async for p in build(quote_max_age_s=60)(factory, tmp_path, db_session):
        card = await p.signal(await p.processor(jev_client(p)))
        assert card.state == CardState.PENDING
        p.w.h.md.set_quote(GOOGL, Decimal("0.70"), Decimal("0.71"), at=T0 - timedelta(minutes=30))
        await p.w.decide(card.card_id)
        assert (await p.card(card.card_id)).state == CardState.INVALIDATED
        assert (await p.events(card.card_id))[-1].detail["reason"] == "quote_stale"
        assert await p.w.orders() == []


async def test_a_quote_that_crosses_before_approval_places_nothing(
    factory: Factory, tmp_path: Path, db_session: AsyncSession
) -> None:
    async for p in build(quote_max_age_s=60)(factory, tmp_path, db_session):
        card = await p.signal(await p.processor(jev_client(p)))
        assert card.state == CardState.PENDING
        p.w.h.md.set_quote(GOOGL, Decimal("0.75"), Decimal("0.70"))  # F086: bid above ask
        await p.w.decide(card.card_id)
        assert (await p.card(card.card_id)).state == CardState.INVALIDATED
        assert (await p.events(card.card_id))[-1].detail["reason"] == "quote_invalid"
        assert await p.w.orders() == []


# --- supersession after approval ----------------------------------------------------------


async def test_an_approved_entry_overtaken_by_a_stop_message_never_executes(p: Paper) -> None:
    processor = await p.processor(jev_client(p))
    env = await p.w.h.message(TEXT, None, author=PROVIDER_A)
    await p.w.h.process(env, processor=processor)
    async with p.w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None

    approvals = p.w.approvals
    real_resume = approvals.resume

    async def hold(*_: Any, **__: Any) -> None:  # the approval commits; the graph has not run yet
        return None

    approvals.resume = hold  # type: ignore[method-assign]
    await p.w.decide(card.card_id)
    approvals.resume = real_resume  # type: ignore[method-assign]
    assert (await p.card(card.card_id)).state == CardState.APPROVED

    stop = await p.w.h.message(
        "stop 0.50",
        [follow_up("stop_set", opened_position(env), stop_level=lit(Decimal("0.50"), "0.50"))],
        at=T0 + timedelta(seconds=3),
        author=PROVIDER_A,
    )
    await p.w.h.process(stop, processor=p.w.processor)  # fixture-interpreted follow-up
    assert (await p.card(card.card_id)).state == CardState.APPROVED  # only pending cards move

    await approvals.resume_approved()  # revalidation now sees the newer message
    final = await p.card(card.card_id)
    assert final.state == CardState.INVALIDATED
    assert (await p.events(card.card_id))[-1].detail["reason"] == "superseded"
    assert [o for o in await p.w.orders() if o.card_id == card.card_id] == []


async def _approved_and_held(p: Paper) -> tuple[Any, Card]:
    """An approved entry whose graph has not run yet (what a restart or a block leaves)."""
    processor = await p.processor(jev_client(p))
    env = await p.w.h.message(TEXT, None, author=PROVIDER_A)
    await p.w.h.process(env, processor=processor)
    async with p.w.h.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None
    approvals = p.w.approvals
    real_resume = approvals.resume

    async def hold(*_: Any, **__: Any) -> None:
        return None

    approvals.resume = hold  # type: ignore[method-assign]
    await p.w.decide(card.card_id)
    approvals.resume = real_resume  # type: ignore[method-assign]
    assert (await p.card(card.card_id)).state == CardState.APPROVED
    return env, card


@pytest.mark.parametrize("what", ["deleted", "corrected"])
async def test_an_approved_entry_whose_message_was_deleted_or_edited_never_executes(
    p: Paper, what: str
) -> None:
    env, card = await _approved_and_held(p)
    at = T0 + timedelta(seconds=5)
    if what == "deleted":
        later = await p.w.h.delete(env, at=at)
    else:
        later = await p.w.h.edit(
            env,
            "Bought GOOGL 357.5C 10/2 @ 0.72",
            [option_entry("GOOGL", "357.5", "357.5C", "10/2", "0.72")],
            at=at,
        )
    await p.w.h.process(later, processor=p.w.processor)
    await p.w.approvals.resume_approved()
    assert (await p.card(card.card_id)).state == CardState.INVALIDATED
    assert (await p.events(card.card_id))[-1].detail["reason"] == what
    assert await p.w.orders() == []


# --- broker: timeout, rejection, mode safety ---------------------------------------------


async def test_a_submit_timeout_queries_the_broker_and_never_resends(p: Paper) -> None:
    p.w.broker.default = Script(Behavior.TIMEOUT_THEN_EXISTS)
    card = await p.signal(await p.processor(jev_client(p)))
    await p.w.decide(card.card_id)
    calls = [name for name, _ in p.w.broker.calls]
    assert calls.count("submit") == 1 and "get_by_client_id" in calls  # FR-22
    [order] = await p.w.orders()
    assert order.status == OrderStatus.WORKING and order.broker_order_id is not None


async def test_a_rejected_order_shows_as_rejected_on_the_card_and_creates_no_position(
    p: Paper,
) -> None:
    p.w.broker.default = Script(Behavior.REJECT)
    card = await p.signal(await p.processor(jev_client(p)))
    await p.w.decide(card.card_id)
    final = await p.card(card.card_id)
    assert final.state == CardState.INVALIDATED  # not "submitted"
    event = (await p.events(card.card_id))[-1]
    assert event.reason_code == CardReason.ORDER_REJECTED
    [order] = await p.w.orders()
    assert order.status == OrderStatus.REJECTED
    await p.w.consume()
    assert await p.lots() == []


async def test_a_paper_mode_that_points_at_the_wrong_broker_is_refused(p: Paper) -> None:
    card = await p.signal(await p.processor(jev_client(p)))
    orders = p.w.approvals._executor._orders
    orders._broker_kind, orders._allow_fake = "alpaca-live", False  # paper mode, live endpoint
    await p.w.decide(card.card_id)
    assert (await p.card(card.card_id)).state == CardState.INVALIDATED
    assert "trading_mode=paper" in (await p.events(card.card_id))[-1].detail["reason"]
    assert await p.w.orders() == []  # nothing stored, nothing sent
    assert [c for c, _ in p.w.broker.calls if c == "submit"] == []


# --- fills: full, partial, duplicate -----------------------------------------------------


async def test_a_partial_fill_makes_a_partial_lot_and_a_duplicate_fill_changes_nothing(
    p: Paper,
) -> None:
    p.w.broker.default = Script(Behavior.PARTIAL, qty=5)
    card = await p.signal(await p.processor(jev_client(p)))
    await p.w.decide(card.card_id)
    events = p.w.broker.pending_events()
    consumer = BrokerEventConsumer(p.w.h.factory, [p.w.broker])
    for event in [*events, *events]:  # the stream delivers every fill twice
        await consumer.handle(event)
    [lot] = await p.lots()
    assert lot.qty == 5
    [order] = await p.w.orders()
    assert (order.status, order.filled_qty) == (OrderStatus.PARTIALLY_FILLED, 5)
    async with p.w.h.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Fill)) == 1


async def test_no_model_ever_sees_the_account(p: Paper, db_session: AsyncSession) -> None:
    """AI interprets, code decides: the model requests carry the message and the provider's book,
    never buying power, equity, cash or the user's lots."""
    await p.signal(await p.processor(jev_down(p.w.h)))  # Jev asks (and retries)
    requests = (await db_session.scalars(select(col(ModelCall.request)))).all()
    assert requests
    sent = repr(requests)
    for secret in ("buying_power", "buyingPower", "equity", "cash", "options_buying_power"):
        assert secret not in sent
