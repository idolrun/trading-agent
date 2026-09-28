from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.instruments import OptionContract, Stock, broker_symbol


@pytest.mark.parametrize(
    ("underlying", "expiry", "strike", "right", "occ"),
    [
        ("GOOGL", date(2026, 10, 2), "357.5", "call", "GOOGL 261002C00357500"),
        ("SPXW", date(2026, 9, 28), "5800", "put", "SPXW  260928P05800000"),
        ("TSLA", date(2026, 10, 16), "400", "call", "TSLA  261016C00400000"),
        ("F", date(2027, 1, 15), "12.5", "put", "F     270115P00012500"),
        ("SPY", date(2026, 9, 29), "580", "call", "SPY   260929C00580000"),
    ],
)
def test_occ_symbol(underlying: str, expiry: date, strike: str, right: str, occ: str) -> None:
    contract = OptionContract(
        underlying=underlying,
        expiry=expiry,
        strike=Decimal(strike),
        right=right,  # type: ignore[arg-type]
    )
    assert contract.occ_symbol() == occ
    assert len(occ) == 21
    assert broker_symbol(contract) == occ


@pytest.mark.parametrize(
    "bad",
    [
        {"strike": Decimal("0.0001")},  # more than 3 decimals
        {"strike": Decimal(0)},
        {"strike": Decimal(100000)},
        {"underlying": "googl"},
        {"underlying": "TOOLONGX"},
        {"right": "straddle"},
    ],
)
def test_invalid_contracts_rejected(bad: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "underlying": "SPY",
        "expiry": date(2026, 10, 2),
        "strike": Decimal(580),
        "right": "call",
    }
    with pytest.raises(ValidationError):
        OptionContract.model_validate(fields | bad)


def test_stock() -> None:
    assert broker_symbol(Stock(symbol="BRK.B")) == "BRK.B"
    with pytest.raises(ValidationError):
        Stock(symbol="tsla")


def test_value_types_are_hashable_and_frozen() -> None:
    a = OptionContract(
        underlying="SPY", expiry=date(2026, 10, 2), strike=Decimal(580), right="call"
    )
    b = OptionContract(
        underlying="SPY", expiry=date(2026, 10, 2), strike=Decimal(580), right="call"
    )
    assert a == b and len({a, b}) == 1
    with pytest.raises(ValidationError):
        a.strike = Decimal(1)  # type: ignore[misc]
