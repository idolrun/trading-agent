"""F023/F085: tick rounding and TP legs for the user's quantity (C56, C83, C105)."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.decide.rules import entry_limit, floor_to_tick, tp_legs

D = Decimal


def test_entry_limit_chases_then_floors_to_tick() -> None:
    assert entry_limit(D("1.67"), D("5"), D("0.01")) == D("1.75")  # 1.7535 -> 1.75
    assert entry_limit(D("3.10"), D("2"), D("0.05")) == D("3.15")  # 3.162 -> 3.15
    assert floor_to_tick(D("1.70"), D("0.05")) == D("1.70")


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
