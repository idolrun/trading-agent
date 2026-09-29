"""F028: FR-20 invariants (AC-3) + property: no sequence of allowed sells shorts or oversells."""

import uuid
from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.execution.intent import Approved, Auto, OrderIntent, client_order_id
from app.execution.invariants import LotView, WorkingSells, excluding, violations
from app.execution.models import AutoReason
from app.instruments import OptionContract, broker_symbol

GOOGL = OptionContract(
    underlying="GOOGL", expiry=date(2026, 10, 2), strike=Decimal("357.5"), right="call"
)
P_A, P_B = uuid.uuid4(), uuid.uuid4()
LOT = LotView(uuid.uuid4(), P_A, broker_symbol(GOOGL), qty=2, is_open=True)


def intent(kind: str = "exit", side: str = "sell", qty: int = 1, **kw: object) -> OrderIntent:
    return OrderIntent.model_validate(
        {
            "kind": kind,
            "side": side,
            "instrument": GOOGL,
            "qty": qty,
            "order_type": "limit",
            "limit_price": Decimal("1"),
            "tif": "day",
            "provider_position_id": P_A,
            **kw,
        }
    )


def test_buy_entry_ok_and_shape_rules() -> None:
    assert violations(intent("entry", "buy"), None, WorkingSells(), None) == []
    assert violations(intent("exit", "buy"), None, WorkingSells(), None) == ["buy_only_for_entries"]
    assert violations(intent("entry", "sell"), LOT, WorkingSells(), 2) == ["entry_must_buy"]


def test_sell_needs_that_providers_open_lot() -> None:
    assert violations(intent(), None, WorkingSells(), 2) == ["no_open_lot"]
    closed = LotView(LOT.lot_id, P_A, LOT.broker_symbol, 0, is_open=False)
    assert violations(intent(), closed, WorkingSells(), 2) == ["no_open_lot"]
    assert violations(intent(provider_position_id=P_B), LOT, WorkingSells(), 2) == [
        "other_providers_lot"  # AC-3: B's contracts never sold against A's lot
    ]
    spy = OptionContract(
        underlying="SPY", expiry=date(2026, 10, 2), strike=Decimal(500), right="put"
    )
    assert "instrument_mismatch" in violations(intent(instrument=spy), LOT, WorkingSells(), 2)


def test_sell_quantities() -> None:
    assert violations(intent(qty=2), LOT, WorkingSells(), 2) == []
    assert violations(intent(qty=3), LOT, WorkingSells(), 3) == ["sell_exceeds_lot"]
    assert violations(intent(qty=1), LOT, WorkingSells(exits=2), 2) == ["sell_exceeds_lot"]
    assert violations(intent("tp", qty=1), LOT, WorkingSells(protective=2), 2) == [
        "protective_exceeds_lot"
    ]
    assert violations(intent(qty=2), LOT, WorkingSells(), 1) == ["broker_position_short"]
    assert violations(intent(qty=1), LOT, WorkingSells(), None) == ["broker_position_short"]


def test_exits_and_protection_never_hold_the_same_contracts() -> None:
    """FR-20(3): exits + protective exposure <= lot on every sell."""
    group = uuid.uuid4()
    for kind in ("stop", "tp"):  # protection while an exit is working
        assert violations(intent(kind, qty=1), LOT, WorkingSells(exits=2), 2) == [
            "sells_exceed_lot"
        ]
        assert violations(intent(kind, qty=1), LOT, WorkingSells(exits=1), 2) == []
    assert violations(intent(qty=1), LOT, WorkingSells(protective=2), 2) == ["sells_exceed_lot"]
    assert violations(intent(qty=1), LOT, WorkingSells(protective=1), 2) == []
    bracket = WorkingSells(groups={group: (2, 2)})  # OCO: stop 2 vs TPs 2 -> exposure 2
    assert violations(intent(qty=1), LOT, bracket, 2) == ["sells_exceed_lot"]
    assert violations(intent("tp", qty=1, oco_group_id=group), LOT, WorkingSells(exits=1), 2) == []


def test_excluding_removes_the_intents_own_row() -> None:
    group = uuid.uuid4()
    working = WorkingSells(exits=2, protective=1, groups={group: (2, 1)})
    assert excluding(working, intent(qty=2)) == WorkingSells(0, 1, {group: (2, 1)})
    assert excluding(working, intent("stop", qty=1)) == WorkingSells(2, 0, {group: (2, 1)})
    stop = intent("stop", qty=2, oco_group_id=group)
    assert excluding(working, stop) == WorkingSells(2, 1, {group: (0, 1)})
    assert excluding(working, intent("entry", "buy")) == working
    # a retried exit re-checks against the others only: its own stored row is not "another"
    assert violations(intent(qty=2), LOT, excluding(WorkingSells(exits=2), intent(qty=2)), 2) == []


def test_client_order_ids_are_deterministic() -> None:
    card, approval = uuid.uuid4(), Approved(uuid.uuid4())
    a = client_order_id(intent("entry", "buy", card_id=card), approval)
    assert a == client_order_id(intent("entry", "buy", card_id=card), Approved(uuid.uuid4()))
    assert a != client_order_id(intent("entry", "buy", card_id=card, action_seq=1), approval)
    parent = uuid.uuid4()
    r1 = client_order_id(intent(parent_order_id=parent), Auto(AutoReason.REPRICE, 1))
    assert r1 != client_order_id(intent(parent_order_id=parent), Auto(AutoReason.REPRICE, 2))
    assert a.startswith("cta-")


@given(
    st.integers(0, 20),
    st.lists(st.tuples(st.sampled_from(["exit", "stop", "fill"]), st.integers(1, 10)), max_size=40),
)
def test_allowed_sells_never_short_or_oversell(start: int, steps: list[tuple[str, int]]) -> None:
    lot, broker = start, start
    working = {"exit": 0, "stop": 0}
    for kind, qty in steps:
        if kind == "fill":  # some working exit qty fills: lot and broker shrink together
            done = min(qty, working["exit"])
            working["exit"] -= done
            lot -= done
            broker -= done
            continue
        view = LotView(LOT.lot_id, P_A, LOT.broker_symbol, lot, is_open=lot > 0)
        state = WorkingSells(exits=working["exit"], protective=working["stop"])
        if not violations(intent(kind, qty=qty), view, state, broker):
            working[kind] += qty
            assert working[kind] <= lot <= broker  # at acceptance nothing is over-committed
        # exits and stops together never oversell the lot (an exit fill shrinks both sides)
        assert working["exit"] + working["stop"] <= lot
        assert lot >= 0 and broker >= 0
