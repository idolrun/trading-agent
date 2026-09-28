"""F022: lot fill arithmetic (FR-16)."""

from decimal import Decimal
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.ledger.lots import apply_fill, cost_basis
from app.ledger.models import Instrument, LotChange, LotStatus


def test_add_recomputes_average() -> None:
    first = apply_fill(None, "buy", 2, Decimal("1.00"))
    assert (first.change, first.qty_after, first.avg_after) == (LotChange.OPEN, 2, Decimal("1"))
    second = apply_fill((2, first.avg_after), "buy", 2, Decimal("2.00"))
    assert (second.change, second.qty_after, second.avg_after) == (
        LotChange.FILL_ADD,
        4,
        Decimal("1.5"),
    )


def test_sell_keeps_average_and_closes_at_zero() -> None:
    part = apply_fill((4, Decimal("1.5")), "sell", 1, Decimal("3"))
    assert (part.change, part.qty_after, part.avg_after, part.status) == (
        LotChange.FILL_REDUCE,
        3,
        Decimal("1.5"),
        LotStatus.OPEN,
    )
    done = apply_fill((3, Decimal("1.5")), "sell", 3, Decimal("3"))
    assert (done.change, done.qty_after, done.status) == (LotChange.CLOSE, 0, LotStatus.CLOSED)


def test_rejects_impossible_fills() -> None:
    with pytest.raises(ValueError):
        apply_fill(None, "sell", 1, Decimal("1"))
    with pytest.raises(ValueError):
        apply_fill(None, "buy", 0, Decimal("1"))
    with pytest.raises(ValueError):
        apply_fill((1, Decimal("1")), "sell", 2, Decimal("1"))


def test_cost_basis() -> None:
    assert cost_basis(Instrument.OPTION, 3, Decimal("1.5")) == Decimal("450")
    assert cost_basis(Instrument.STOCK, 3, Decimal("1.5")) == Decimal("4.5")
    assert cost_basis(Instrument.OPTION, 3, None) is None


prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("5000"), places=2)
fills = st.lists(
    st.tuples(st.sampled_from(["buy", "sell"]), st.integers(1, 50), prices), max_size=30
)


@given(fills)
def test_qty_never_negative_and_avg_within_buy_prices(
    steps: list[tuple[Literal["buy", "sell"], int, Decimal]],
) -> None:
    held: tuple[int, Decimal | None] | None = None
    bought: list[Decimal] = []
    for side, qty, price in steps:
        if side == "sell":
            if held is None:
                continue
            qty = min(qty, held[0])  # F028 never lets a sell exceed the lot
        update = apply_fill(held, side, qty, price)
        assert update.qty_after >= 0
        if side == "buy":
            bought.append(price)
        if update.avg_after is not None:
            assert min(bought) <= update.avg_after <= max(bought)
        held = None if update.status == LotStatus.CLOSED else (update.qty_after, update.avg_after)
        if held is None:
            bought = []
