"""F023: sizing, tick rounding, fractional exits and TP legs (C15, C51, C56, C82, C83)."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.decide.rules import entry_limit, exit_qty, floor_to_tick, size, tp_legs

D = Decimal


def test_entry_limit_chases_then_floors_to_tick() -> None:
    assert entry_limit(D("1.67"), D("5"), D("0.01")) == D("1.75")  # 1.7535 -> 1.75
    assert entry_limit(D("3.10"), D("2"), D("0.05")) == D("3.15")  # 3.162 -> 3.15
    assert floor_to_tick(D("1.70"), D("0.05")) == D("1.70")


def test_option_and_stock_sizing_never_round_up() -> None:
    assert size(D("1000"), D("1.75"), option=True) == 5
    assert size(D("100"), D("2.50"), option=True) == 0  # tier cannot buy one contract
    assert size(D("5000"), D("180.25"), option=False) == 27


def test_exit_qty() -> None:
    assert exit_qty("half", 5) == 2
    assert exit_qty("third", 3) == 1
    assert exit_qty("half", 1) == 0  # C82: lot too small
    assert exit_qty("all", 7) == 7
    assert exit_qty("not_stated", 4) is None and exit_qty("other_stated", 4) is None
    assert exit_qty(None, 4) is None


def test_tp_leg_examples() -> None:
    levels = [D("2"), D("2.5"), D("3")]
    assert [(leg.level, leg.qty) for leg in tp_legs(levels, 5)] == [
        (D("2"), 2),
        (D("2.5"), 2),
        (D("3"), 1),
    ]
    assert [leg.qty for leg in tp_legs(levels, 2)] == [1, 1]  # AC-16: no TP3
    assert [(leg.level, leg.qty) for leg in tp_legs(levels, 1)] == [(D("2"), 1)]
    assert tp_legs(levels, None) == [] and tp_legs([], 3) == []


@given(st.integers(1, 500), st.integers(1, 10))
def test_tp_legs_properties(qty: int, n_levels: int) -> None:
    legs = tp_legs([D(i + 1) for i in range(n_levels)], qty)
    sizes = [leg.qty for leg in legs]
    assert sum(sizes) == qty
    assert len(legs) == min(n_levels, qty)
    assert all(q >= 1 for q in sizes)
    assert sizes == sorted(sizes, reverse=True)  # non-increasing from TP1
