"""F046: the pure reconciliation rules (no I/O)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.broker.port import BrokerOrder, BrokerPosition
from app.execution.models import Order, OrderStatus, Side
from app.instruments import Stock
from app.ledger.models import Instrument, Lot, LotStatus
from app.reconcile.service import Mismatch, compare, order_events

AT = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def position(symbol: str, qty: int, avg: str | None = "1.50") -> BrokerPosition:
    return BrokerPosition(Stock(symbol=symbol), qty, Decimal(avg) if avg else None)


def lot(symbol: str, qty: int, *, foreign: bool = False) -> Lot:
    return Lot(
        lot_id=uuid.uuid4(),
        is_foreign=foreign,
        instrument=Instrument.STOCK,
        broker_symbol=symbol,
        underlying=symbol,
        qty=qty,
        avg_price=Decimal("1.00"),
        status=LotStatus.OPEN,
    )


def broker_order(
    coid: str, status: OrderStatus, filled: int = 0, avg: str | None = None
) -> BrokerOrder:
    return BrokerOrder(
        "B-1",
        coid,
        Stock(symbol="SPY"),
        Side.BUY,
        10,
        status,
        filled,
        Decimal(avg) if avg else None,
    )


def test_matching_quantities_are_clean() -> None:
    report = compare(
        [position("SPY", 5, "1.00")], [lot("SPY", 3), lot("SPY", 2, foreign=True)], [], set()
    )
    assert report.clean and report.foreign == ()


def test_broker_holding_without_lots_becomes_a_foreign_lot() -> None:
    report = compare([position("SPY", 5)], [], [], set())
    [change] = report.foreign
    assert report.clean and (change.qty, change.avg, change.lot_id) == (5, Decimal("1.50"), None)


def test_foreign_lot_follows_the_broker_and_closes_at_zero() -> None:
    held = lot("SPY", 5, foreign=True)
    [grown] = compare([position("SPY", 8)], [held], [], set()).foreign
    assert (grown.lot_id, grown.qty) == (held.lot_id, 8)
    [closed] = compare([], [held], [], set()).foreign
    assert (closed.lot_id, closed.qty, closed.avg) == (held.lot_id, 0, Decimal("1.00"))


def test_tracked_lot_quantity_difference_is_a_mismatch() -> None:
    report = compare([position("SPY", 5)], [lot("SPY", 3)], [], set())
    assert report.mismatches == (Mismatch("SPY", 5, 3),) and not report.clean
    assert report.foreign == ()  # never adjusted silently


def test_tracked_lot_the_broker_no_longer_holds_is_a_mismatch() -> None:
    report = compare([], [lot("SPY", 2)], [], set())
    assert report.mismatches == (Mismatch("SPY", 0, 2),)


def test_tracked_plus_untracked_broker_excess_is_a_mismatch() -> None:
    report = compare([position("SPY", 6)], [lot("SPY", 3), lot("SPY", 1, foreign=True)], [], set())
    assert report.mismatches == (Mismatch("SPY", 6, 4),)


def test_short_position_is_a_mismatch() -> None:
    assert compare([position("SPY", -1)], [], [], set()).mismatches == (Mismatch("SPY", -1, 0),)


def test_unknown_cta_order_is_reported_other_prefixes_are_not_ours() -> None:
    orders = [
        broker_order("cta-known", OrderStatus.WORKING),
        broker_order("cta-ghost", OrderStatus.WORKING),
        broker_order("manual-1", OrderStatus.WORKING),
    ]
    report = compare([], [], orders, {"cta-known"})
    assert report.unknown_orders == ("cta-ghost",) and not report.clean


def db_order(status: OrderStatus, filled: int = 0, avg: str | None = None) -> Order:
    return Order(
        client_order_id="cta-1",
        status=status,
        filled_qty=filled,
        avg_fill_price=Decimal(avg) if avg else None,
    )


def test_order_events_fill_uses_a_deterministic_id_and_the_missing_quantity() -> None:
    order = db_order(OrderStatus.WORKING)
    found = broker_order("cta-1", OrderStatus.FILLED, 10, "0.71")
    [fill] = order_events(order, 0, found, AT)
    assert (fill.kind, fill.fill_id, fill.qty, fill.price) == (
        "fill",
        "reconcile:cta-1:10",
        10,
        Decimal("0.71"),
    )
    assert order_events(db_order(OrderStatus.FILLED, 10, "0.71"), 10, found, AT) == []


def test_order_events_partial_progress_prices_only_the_missing_part() -> None:
    order = db_order(OrderStatus.PARTIALLY_FILLED, 4, "1.00")
    found = broker_order("cta-1", OrderStatus.PARTIALLY_FILLED, 10, "1.30")
    [fill] = order_events(order, 4, found, AT)
    assert (fill.qty, fill.price) == (6, Decimal("1.50"))  # (13.0 - 4.0) / 6


def test_order_events_status_only_and_cancel_after_partial() -> None:
    [status] = order_events(
        db_order(OrderStatus.UNKNOWN), 0, broker_order("cta-1", OrderStatus.WORKING), AT
    )
    assert (status.kind, status.status) == ("status", OrderStatus.WORKING)
    fill, cancel = order_events(
        db_order(OrderStatus.WORKING),
        0,
        broker_order("cta-1", OrderStatus.CANCELLED, 3, "1.00"),
        AT,
    )
    assert (fill.kind, cancel.kind, cancel.status) == ("fill", "status", OrderStatus.CANCELLED)


def test_equal_quantities_with_a_different_average_cost_are_a_mismatch() -> None:
    report = compare([position("SPY", 3, "1.50")], [lot("SPY", 3)], [], set())  # lots: 1.00
    [m] = report.mismatches
    assert (m.symbol, m.broker_qty, m.lot_qty) == ("SPY", 3, 3)
    assert (m.broker_avg, m.lot_avg) == (Decimal("1.50"), Decimal("1.0000"))
    assert not report.clean


def test_average_cost_within_the_tolerance_or_unknown_is_clean() -> None:
    assert compare([position("SPY", 3, "1.005")], [lot("SPY", 3)], [], set()).clean  # noise
    assert compare([position("SPY", 3, None)], [lot("SPY", 3)], [], set()).clean  # not stated
    foreign = [lot("SPY", 3, foreign=True)]
    assert compare([position("SPY", 3, "9.99")], foreign, [], set()).clean  # the broker's own
