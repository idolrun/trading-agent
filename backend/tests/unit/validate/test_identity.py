from datetime import UTC, date, datetime
from decimal import Decimal

from app.instruments import OptionContract
from app.marketdata.port import Quote
from app.validate.identity import adjacent_strike_fits_better, find_in_chain
from app.validate.underlying_suggest import suggest

AT = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
EXP = date(2026, 10, 2)


def leg(strike: str, mid: str, right: str = "call") -> tuple[OptionContract, Quote]:
    m = Decimal(mid)
    contract = OptionContract(underlying="GOOGL", expiry=EXP, strike=Decimal(strike), right=right)  # type: ignore[arg-type]
    return contract, Quote(bid=m, ask=m, last=None, at=AT)


CHAIN = [
    leg("352.5", "4.10"),
    leg("355", "3.00"),
    leg("357.5", "2.10"),
    leg("360", "1.40"),
    leg("355", "2.9", "put"),
]


def test_find_in_chain() -> None:
    found = find_in_chain(CHAIN, Decimal("357.5"), "call")
    assert found is not None and found[0].strike == Decimal("357.5")
    assert find_in_chain(CHAIN, Decimal("357.5"), "put") is None
    assert find_in_chain(CHAIN, Decimal(358), "call") is None


def test_adjacent_strike_warning_rule() -> None:
    contract = CHAIN[2][0]  # 357.5C, mid 2.10
    assert adjacent_strike_fits_better(CHAIN, contract, Decimal("3.00"), Decimal("0.5"))  # = 355C
    assert not adjacent_strike_fits_better(CHAIN, contract, Decimal("2.10"), Decimal("0.5"))
    assert not adjacent_strike_fits_better(CHAIN, contract, Decimal("2.50"), Decimal("0.5"))


def test_suggestion_needs_exactly_one_match() -> None:
    assert suggest(Decimal(1000), {"MU": Decimal(995), "TSLA": Decimal(402)}, Decimal(10)) == "MU"
    assert suggest(Decimal(1000), {"MU": Decimal(995), "AVGO": Decimal(1040)}, Decimal(10)) is None
    assert suggest(Decimal(1000), {"TSLA": Decimal(402)}, Decimal(10)) is None
    assert suggest(Decimal(1000), {}, Decimal(10)) is None
    # boundary: exactly P% away counts
    assert suggest(Decimal(110), {"X": Decimal(100)}, Decimal(10)) == "X"
