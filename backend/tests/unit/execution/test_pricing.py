"""F043: the exit price path is monotone, tick-aligned and never below the floor."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.execution.pricing import floor_price, next_price, round_to_tick

TICKS = st.sampled_from([Decimal("0.01"), Decimal("0.05"), Decimal("0.10")])
PRICES = st.decimals(min_value=Decimal("0.05"), max_value=Decimal("50"), places=2)


def test_examples() -> None:
    tick = Decimal("0.01")
    assert round_to_tick(Decimal("0.555"), tick) == Decimal("0.56")
    assert floor_price(Decimal("0.555"), Decimal("20"), tick) == Decimal("0.45")
    assert floor_price(Decimal("0.02"), Decimal("90"), tick) == tick  # never below one tick
    assert next_price(
        Decimal("0.56"), Decimal("0.555"), Decimal("0.40"), Decimal("2"), Decimal("0.45"), tick
    ) == Decimal("0.55")


@given(
    bid=PRICES,
    spread=PRICES,
    pct=st.decimals(1, 90, places=0),
    step=st.decimals(1, 50, places=0),
    tick=TICKS,
)
def test_path_is_monotone_on_grid_and_floored(
    bid: Decimal, spread: Decimal, pct: Decimal, step: Decimal, tick: Decimal
) -> None:
    mid = bid + spread / 2
    floor = floor_price(mid, pct, tick)
    price = max(floor, round_to_tick(mid, tick))
    for _ in range(100_000):
        new = next_price(price, mid, bid, step, floor, tick)
        assert floor <= new <= price
        assert (new / tick) % 1 == 0 or new == floor
        if new == price:
            assert new == floor
            break
        price = new
    assert price == floor
