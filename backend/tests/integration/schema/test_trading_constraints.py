"""F005: trading state & audit tables (database-schema.md §4) and their safety constraints."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import errors
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cards.models import Approval, Card
from app.execution.models import Fill, Order
from app.interpret.models import MessageLink, Signal
from app.ledger.models import Lot, ProviderPosition, ProviderPositionEvent
from tests.integration.schema.helpers import T0, add_session, add_signal, expect_error

GOOGL = {
    "instrument": "option",
    "underlying": "GOOGL",
    "expiry": date(2026, 10, 2),
    "strike": Decimal("357.5"),
    "option_right": "call",
}


async def add_position(session: AsyncSession, signal: Signal) -> ProviderPosition:
    position = ProviderPosition(
        provider_id=signal.provider_id,
        status="open",
        provider_entry_price=Decimal("1.67"),
        opened_by_signal_id=signal.signal_id,
        **GOOGL,
    )
    session.add(position)
    await session.commit()
    return position


async def add_lot(session: AsyncSession, position: ProviderPosition, **overrides: Any) -> Lot:
    lot = Lot(
        provider_position_id=position.provider_position_id,
        provider_id=position.provider_id,
        is_foreign=False,
        broker_symbol="GOOGL 261002C00357500",
        qty=overrides.pop("qty", 2),
        avg_price=Decimal("1.67"),
        status=overrides.pop("status", "open"),
        **GOOGL,
        **overrides,
    )
    session.add(lot)
    await session.commit()
    return lot


async def add_card(session: AsyncSession, signal: Signal, action: str = "entry") -> Card:
    key = f"sig:{signal.signal_id}:{action}"
    card = Card(
        card_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
        dedupe_key=key,
        source="signal",
        signal_id=signal.signal_id,
        action=action,
        card_type="normal",
        state="pending",
        provider_id=signal.provider_id,
        proposal={"qty": 2},
        posted_at=T0,
    )
    session.add(card)
    await session.commit()
    return card


async def approve(session: AsyncSession, card: Card) -> Approval:
    approval = Approval(
        card_id=card.card_id,
        decision="approve",
        approved_proposal=card.proposal,
        card_version=card.version,
        session_id=await add_session(session),
    )
    session.add(approval)
    await session.commit()
    return approval


ORDER_SQL = """
    INSERT INTO orders (order_id, client_order_id, approval_id, auto_reason, kind, side,
        instrument, broker_symbol, qty, order_type, limit_price, tif, status, mode)
    VALUES (gen_random_uuid(), :coid, :approval_id, :auto_reason, :kind, :side, 'option',
        'GOOGL 261002C00357500', 1, 'limit', 1.70, 'day', 'intent', 'shadow')
"""


def order_params(**overrides: Any) -> dict[str, Any]:
    return {
        "coid": f"cta-{uuid.uuid4()}",
        "approval_id": None,
        "auto_reason": None,
        "kind": "entry",
        "side": "buy",
    } | overrides


async def test_link_cannot_cross_providers(db_session: AsyncSession) -> None:
    signal_a = await add_signal(db_session, provider_id=7, message_id=1001)
    signal_b = await add_signal(db_session, provider_id=8, message_id=1002)
    position_b = await add_position(db_session, signal_b)
    await expect_error(
        db_session,
        errors.ForeignKeyViolation,
        """INSERT INTO message_links (signal_id, provider_id, provider_position_id, outcome, method)
           VALUES (:sid, 7, :pid, 'linked', 'jev')""",
        {"sid": signal_a.signal_id, "pid": position_b.provider_position_id},
    )
    db_session.add(
        MessageLink(
            signal_id=signal_b.signal_id,
            provider_id=8,
            provider_position_id=position_b.provider_position_id,
            outcome="new_position",
            method="code",
            confidence=Decimal(1),
        )
    )
    await db_session.commit()


async def test_one_open_lot_per_provider_position(db_session: AsyncSession) -> None:
    position = await add_position(db_session, await add_signal(db_session))
    first = await add_lot(db_session, position)
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO lots (lot_id, provider_position_id, provider_id, is_foreign, instrument,
               broker_symbol, underlying, expiry, strike, option_right, qty, status)
           VALUES (gen_random_uuid(), :pid, 7, false, 'option', 'X', 'GOOGL', '2026-10-02',
               357.5, 'call', 1, 'open')""",
        {"pid": position.provider_position_id},
    )
    await db_session.execute(
        text("UPDATE lots SET qty = 0, status = 'closed' WHERE lot_id = :id"),
        {"id": first.lot_id},
    )
    await db_session.commit()
    await add_lot(db_session, position)  # re-entry after full exit = new lot (C62)


async def test_lot_shape_checks(db_session: AsyncSession) -> None:
    position = await add_position(db_session, await add_signal(db_session))
    sql = """
        INSERT INTO lots (lot_id, provider_position_id, provider_id, is_foreign, instrument,
            broker_symbol, underlying, expiry, strike, option_right, qty, status)
        VALUES (gen_random_uuid(), :pid, :prov, :foreign, 'option', 'X', 'GOOGL',
            '2026-10-02', 357.5, :right, :qty, :status)
    """
    ok = {
        "pid": None,
        "prov": None,
        "foreign": True,
        "right": "call",
        "qty": 1,
        "status": "open",
    }
    bad = [
        ok | {"foreign": False},  # foreign iff no provider position
        ok | {"qty": 0},  # open lot must hold something
        ok | {"status": "closed"},  # closed lot must be empty
        ok | {"right": None},  # option needs its full contract
        ok | {"pid": position.provider_position_id, "prov": 7},  # tracked lot is not foreign
        ok | {"pid": position.provider_position_id, "prov": None, "foreign": False},
    ]
    for params in bad:
        await expect_error(db_session, errors.CheckViolation, sql, params)
    await expect_error(  # a lot cannot claim another provider's position
        db_session,
        errors.ForeignKeyViolation,
        sql,
        ok | {"pid": position.provider_position_id, "prov": 8, "foreign": False},
    )
    await db_session.execute(text(sql), ok)  # foreign lot (C47)
    await db_session.commit()


async def test_order_needs_approval_or_allowed_auto_reason(db_session: AsyncSession) -> None:
    card = await add_card(db_session, await add_signal(db_session))
    approval = await approve(db_session, card)
    await expect_error(db_session, errors.CheckViolation, ORDER_SQL, order_params())
    await expect_error(
        db_session,
        errors.CheckViolation,
        ORDER_SQL,
        order_params(auto_reason="other", kind="exit", side="sell"),
    )
    await db_session.execute(text(ORDER_SQL), order_params(approval_id=approval.approval_id))
    await db_session.execute(
        text(ORDER_SQL), order_params(auto_reason="eod_forced_close", kind="exit", side="sell")
    )
    await db_session.commit()


async def test_no_short_shape(db_session: AsyncSession) -> None:
    for kind, side in (("exit", "buy"), ("stop", "buy"), ("entry", "sell")):
        await expect_error(
            db_session,
            errors.CheckViolation,
            ORDER_SQL,
            order_params(auto_reason="reprice", kind=kind, side=side),
        )


async def test_order_and_fill_idempotency_keys(db_session: AsyncSession) -> None:
    params = order_params(auto_reason="reprice", kind="exit", side="sell", coid="cta-1")
    await db_session.execute(text(ORDER_SQL), params)
    await db_session.commit()
    await expect_error(db_session, errors.UniqueViolation, ORDER_SQL, params)

    order = Order(
        client_order_id="cta-2",
        auto_reason="stop_restore",
        kind="stop",
        side="sell",
        instrument="option",
        broker_symbol="X",
        qty=1,
        order_type="stop_market",
        tif="gtc",
        status="working",
        mode="paper",
        broker_order_id="B-1",
    )
    db_session.add(order)
    await db_session.commit()
    db_session.add(
        Fill(
            order_id=order.order_id,
            broker_fill_id="F-1",
            qty=1,
            price=Decimal("1.2"),
            filled_at=T0,
        )
    )
    await db_session.commit()
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO fills (fill_id, order_id, broker_fill_id, qty, price, filled_at)
           VALUES (gen_random_uuid(), :oid, 'F-1', 1, 1.2, now())""",
        {"oid": order.order_id},
    )
    await expect_error(
        db_session,
        errors.CheckViolation,
        "UPDATE orders SET filled_qty = 2 WHERE order_id = :oid",
        {"oid": order.order_id},
    )


async def test_card_uniqueness_and_shape(db_session: AsyncSession) -> None:
    signal = await add_signal(db_session)
    card = await add_card(db_session, signal)
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO cards (card_id, dedupe_key, source, signal_id, action, card_type, state,
               proposal, posted_at)
           VALUES (gen_random_uuid(), 'other-key', 'signal', :sid, 'entry', 'normal',
               'pending', '{}', now())""",
        {"sid": signal.signal_id},
    )
    sql = """
        INSERT INTO cards (card_id, dedupe_key, source, signal_id, action, card_type, state,
            disabled_reason, proposal, posted_at)
        VALUES (gen_random_uuid(), :key, :source, NULL, 'review', :type, :state, :reason,
            '{}', now())
    """
    bad = [
        {"key": card.dedupe_key, "source": "system", "type": "review", "state": "pending"},
        {"key": "k1", "source": "signal", "type": "review", "state": "pending"},
        {"key": "k2", "source": "system", "type": "disabled", "state": "pending"},
        {"key": "k3", "source": "system", "type": "review", "state": "disabled"},
        {"key": "k4", "source": "system", "type": "disabled", "state": "disabled"},  # no reason
    ]
    errors_expected = [errors.UniqueViolation] + [errors.CheckViolation] * 4
    for params, error in zip(bad, errors_expected, strict=True):
        await expect_error(db_session, error, sql, params | {"reason": None})
    await db_session.execute(
        text(sql),
        {
            "key": "k5",
            "source": "system",
            "type": "disabled",
            "state": "disabled",
            "reason": "config incomplete",
        },
    )
    await db_session.commit()


async def test_card_decided_once(db_session: AsyncSession) -> None:
    card = await add_card(db_session, await add_signal(db_session))
    approval = await approve(db_session, card)
    await expect_error(
        db_session,
        errors.UniqueViolation,
        """INSERT INTO approvals (approval_id, card_id, decision, card_version, session_id)
           VALUES (gen_random_uuid(), :cid, 'reject', 1, :sid)""",
        {"cid": card.card_id, "sid": approval.session_id},
    )


async def test_position_event_has_exactly_one_cause(db_session: AsyncSession) -> None:
    signal = await add_signal(db_session)
    position = await add_position(db_session, signal)
    db_session.add(
        ProviderPositionEvent(
            provider_position_id=position.provider_position_id,
            change="open",
            signal_id=signal.signal_id,
            after={"status": "open"},
        )
    )
    await db_session.commit()
    await expect_error(
        db_session,
        errors.CheckViolation,
        """INSERT INTO provider_position_events (provider_position_id, change, after)
           VALUES (:pid, 'correct', '{}')""",
        {"pid": position.provider_position_id},
    )


async def test_timer_dedupe_key(db_session: AsyncSession) -> None:
    sql = """
        INSERT INTO timers (timer_id, kind, due_at, dedupe_key, status)
        VALUES (gen_random_uuid(), 'cutoff_1', now(), 'cutoff1:lot:2026-09-28', 'scheduled')
    """
    await db_session.execute(text(sql))
    await db_session.commit()
    await expect_error(db_session, errors.UniqueViolation, sql)


async def test_audit_tables_insert_only_for_app_role(db_session: AsyncSession) -> None:
    for sql in (
        "UPDATE approvals SET step_up = true",
        "UPDATE fills SET qty = 1",
        "UPDATE card_events SET actor = 'user'",
        "UPDATE order_events SET source = 'poll'",
        "DELETE FROM lots",
        "DELETE FROM orders",
    ):
        await expect_error(db_session, errors.InsufficientPrivilege, sql)
