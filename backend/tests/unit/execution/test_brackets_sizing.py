"""F042 / OD-30: bracket legs never protect more than the (capped) lot."""

import uuid
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.execution.brackets import Leg, StopSpec, _order_type, size_legs
from app.execution.intent import OrderIntent
from app.execution.invariants import WorkingSells, protective_exposure
from app.execution.models import HeldBy, OrderKind, OrderType, Side, StopKind, StopLevelBasis
from app.instruments import Stock

CARD = uuid.uuid4()
STOP = (CARD, StopSpec(Decimal("0.5"), StopLevelBasis.PREMIUM, HeldBy.BROKER))


def tps(n: int) -> list[tuple[int, Decimal, uuid.UUID]]:
    return [(i + 1, Decimal(i + 1), CARD) for i in range(n)]


def test_ac16_two_contracts_three_tps() -> None:
    legs = size_legs(STOP, tps(3), 2)
    assert [(leg.kind, leg.tp_index, leg.qty) for leg in legs] == [
        (StopKind.STOP, None, 2),
        (StopKind.TP, 1, 1),
        (StopKind.TP, 2, 1),
    ]


@given(
    lot=st.integers(0, 50),
    cap=st.none() | st.integers(0, 50),
    n=st.integers(0, 5),
    with_stop=st.booleans(),
)
def test_exposure_never_exceeds_the_capped_lot(
    lot: int, cap: int | None, n: int, with_stop: bool
) -> None:
    legs = size_legs(STOP if with_stop else None, tps(n), lot, cap)
    limit = min(lot, lot if cap is None else cap)
    stop = sum(leg.qty for leg in legs if leg.kind == StopKind.STOP)
    tp = sum(leg.qty for leg in legs if leg.kind == StopKind.TP)
    assert max(stop, tp) <= limit and all(leg.qty > 0 for leg in legs)


def test_group_counts_max_of_stop_and_tps_ungrouped_add_up() -> None:
    group = uuid.uuid4()
    working = WorkingSells(protective=1, groups={group: (2, 1)})
    assert protective_exposure(working) == 3
    tp = OrderIntent(
        kind=OrderKind.TP,
        side=Side.SELL,
        instrument=Stock(symbol="SPY"),
        qty=1,
        order_type=OrderType.LIMIT,
        tif="gtc",
        oco_group_id=group,
    )
    assert protective_exposure(working, tp) == 3  # TPs 2 = stop 2: still covered
    assert protective_exposure(working, tp.model_copy(update={"qty": 2})) == 4


def test_stock_stop_is_stop_market() -> None:
    leg = Leg(StopKind.STOP, None, Decimal(100), 5, CARD, StopLevelBasis.STOCK, HeldBy.BROKER)
    assert _order_type(leg) == OrderType.STOP_MARKET
