"""F020: provider position transitions (TRD §6 state machine)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.ledger.models import PositionChange
from app.ledger.positions import (
    InvalidTransition,
    PositionState,
    apply_correction,
    apply_to_position,
)
from app.validate.normalize import NormalizedSignal, Price

EXP = date(2026, 10, 2)


def price(v: str) -> Price:
    return Price(raw=Decimal(v), value=Decimal(v), scale="as_is")


def sig(msg_type: str, **fields: Any) -> NormalizedSignal:
    return NormalizedSignal(
        msg_type=msg_type, instrument=fields.pop("instrument", "option"), **fields
    )


ENTRY = sig(
    "entry",
    underlying="GOOGL",
    expiry=EXP,
    strike=Decimal("357.5"),
    right="call",
    entry_price=price("1.67"),
    stop_level=price("1.20"),
    tp_levels=(price("2.10"), price("2.50")),
)


def opened() -> PositionState:
    update = apply_to_position(None, ENTRY)
    assert update is not None
    return update.after


def test_entry_opens_position() -> None:
    update = apply_to_position(None, ENTRY)
    assert update is not None and update.change == PositionChange.OPEN and update.before is None
    p = update.after
    assert (p.underlying, p.expiry, p.strike, p.option_right) == (
        "GOOGL",
        EXP,
        Decimal("357.5"),
        "call",
    )
    assert (p.provider_entry_price, p.stop_level, p.stop_basis) == (
        Decimal("1.67"),
        Decimal("1.20"),
        "premium",
    )
    assert p.tp_levels == (Decimal("2.10"), Decimal("2.50"))
    assert (p.status, p.remaining_fraction) == ("open", Decimal(1))


def test_stock_entry() -> None:
    update = apply_to_position(
        None, sig("stock_entry", instrument="stock", underlying="TSLA", entry_price=price("402.5"))
    )
    assert update is not None
    assert (update.after.instrument, update.after.strike) == ("stock", None)


def test_entry_on_open_position_is_an_add() -> None:
    again = sig("entry", underlying="GOOGL", strike=Decimal("357.5"), entry_price=price("1.40"))
    update = apply_to_position(opened(), again)
    assert update is not None
    assert (update.change, update.add_price, update.after) == (
        PositionChange.ADD,
        Decimal("1.40"),
        opened(),
    )
    add = apply_to_position(opened(), sig("add", entry_price=price("1.30")))
    assert add is not None and add.change == PositionChange.ADD


def test_trims() -> None:
    half = sig("trim", exit_fraction="half")
    once = apply_to_position(opened(), half)
    assert once is not None and (once.after.status, once.after.remaining_fraction) == (
        "trimmed",
        Decimal("0.5"),
    )
    twice = apply_to_position(once.after, half)
    assert twice is not None and twice.after.remaining_fraction == Decimal("0.25")
    third = apply_to_position(opened(), sig("trim", exit_fraction="third"))
    assert third is not None and third.after.remaining_fraction == Decimal("0.6667")
    unknown = apply_to_position(once.after, sig("trim", exit_fraction="not_stated"))
    assert unknown is not None and unknown.after.remaining_fraction == Decimal("0.5")
    everything = apply_to_position(opened(), sig("trim", exit_fraction="all"))
    assert everything is not None and everything.change == PositionChange.EXIT


def test_exits_close() -> None:
    for t in ("full_exit", "stock_exit"):
        update = apply_to_position(opened(), sig(t))
        assert update is not None and (update.change, update.after.status) == (
            PositionChange.EXIT,
            "closed",
        )


def test_no_change_messages() -> None:
    assert apply_to_position(opened(), sig("soft_exit")) is None  # C52: provider still holds
    assert apply_to_position(opened(), sig("tp_hit_notice")) is None  # OD-18
    assert apply_to_position(opened(), sig("commentary_pnl")) is None


def test_stops_and_tps() -> None:
    moved = apply_to_position(opened(), sig("stop_move", stop_level=price("1.50")))
    assert moved is not None and (moved.change, moved.after.stop_level) == (
        PositionChange.STOP_MOVE,
        Decimal("1.50"),
    )
    be = apply_to_position(opened(), sig("stop_move", breakeven_stop=True))
    assert be is not None and be.after.stop_level == Decimal("1.67")
    under = apply_to_position(
        opened(), sig("stop_set", stop_level=price("5790"), stop_basis="underlying")
    )
    assert under is not None and under.after.stop_basis == "underlying"
    cancel = apply_to_position(opened(), sig("stop_cancel"))
    assert cancel is not None and (cancel.after.stop_level, cancel.after.stop_basis) == (None, None)
    removed = apply_to_position(opened(), sig("stop_move", stop_removed=True))
    assert removed is not None and removed.change == PositionChange.STOP_CANCEL
    tps = apply_to_position(opened(), sig("tp_set", tp_levels=(price("3.00"),)))
    assert tps is not None and tps.after.tp_levels == (Decimal("3.00"),)


@pytest.mark.parametrize(
    ("position", "signal"),
    [
        (None, sig("trim", exit_fraction="half")),
        (None, sig("add")),
        (None, sig("stop_set", stop_level=price("1"))),
        ("closed", sig("trim", exit_fraction="half")),
        ("closed", sig("entry", strike=Decimal("357.5"))),
        ("open", sig("stop_set")),  # no level
        ("open", sig("tp_set")),  # no levels
        ("open", sig("entry", strike=Decimal(360))),  # different contract
        (None, sig("entry", underlying="GOOGL")),  # incomplete option contract
    ],
)
def test_illegal_transitions_raise(position: str | None, signal: NormalizedSignal) -> None:
    state = None
    if position == "open":
        state = opened()
    elif position == "closed":
        state = opened().model_copy(update={"status": "closed"})
    with pytest.raises(InvalidTransition):
        apply_to_position(state, signal)


def test_manual_correction() -> None:
    fixed = apply_correction(opened(), {"strike": Decimal(355)})
    assert (fixed.change, fixed.after.strike, fixed.after.is_corrected) == (
        PositionChange.CORRECT,
        Decimal(355),
        True,
    )
    created = apply_correction(None, {"instrument": "stock", "underlying": "MU"})
    assert created.change == PositionChange.OPEN and created.after.is_corrected
