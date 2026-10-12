"""F025: C22 validity from post time, price band, supersession and approval blockers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.cards.models import CardAction, CardReason, CardType
from app.cards.validity import approval_blockers, check_entry, check_exit, supersedes
from app.decide.types import ProposedAction, StopProposal
from app.interpret.models import MsgType
from app.marketdata.port import Quote

D = Decimal
POSTED = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def card(action: CardAction, **kw: object) -> ProposedAction:
    return ProposedAction.model_validate({"action": action, "card_type": CardType.NORMAL, **kw})


def quote(ask: str) -> Quote:
    return Quote(bid=None, ask=D(ask), last=None, at=POSTED)


ENTRY = card(
    CardAction.ENTRY, qty=5, signal_price=D("1.67"), valid_until=POSTED + timedelta(seconds=30)
)


def test_validity_measured_from_post_time() -> None:
    """Card created 20 s after post with N=30 dies 10 s later, not 30."""
    created = POSTED + timedelta(seconds=20)
    assert check_entry(ENTRY, None, created, D("5")) is None
    assert check_entry(ENTRY, None, created + timedelta(seconds=10), D("5")) is None  # boundary
    late = created + timedelta(seconds=10, microseconds=1)
    assert check_entry(ENTRY, None, late, D("5")) == CardReason.TIMEOUT


def test_price_band() -> None:
    now = POSTED + timedelta(seconds=5)
    assert check_entry(ENTRY, quote("1.75"), now, D("5")) is None  # 1.7535 band
    assert check_entry(ENTRY, quote("1.76"), now, D("5")) == CardReason.PRICE_BAND


def test_exit_never_invalid_by_price() -> None:
    exit_card = card(CardAction.EXIT, qty=2, valid_until=POSTED + timedelta(seconds=300))
    now = POSTED + timedelta(seconds=10)
    assert check_exit(exit_card, position_closed=False, now=now) is None
    assert check_exit(exit_card, position_closed=True, now=now) == CardReason.SUPERSEDED
    assert check_exit(exit_card, False, POSTED + timedelta(seconds=301)) == CardReason.TIMEOUT
    stop = card(CardAction.STOP, valid_until=POSTED + timedelta(seconds=300))
    assert check_exit(stop, position_closed=True, now=now) is None


def test_supersession() -> None:
    assert supersedes(MsgType.TRIM, CardAction.ENTRY)
    assert supersedes(MsgType.STOP_MOVE, CardAction.ADD)
    assert supersedes(MsgType.CORRECTION, CardAction.ENTRY)
    assert not supersedes(MsgType.TRIM, CardAction.EXIT)
    assert not supersedes(MsgType.TP_SET, CardAction.ENTRY)
    assert not supersedes(MsgType.SOFT_EXIT, CardAction.ENTRY)


def test_approval_blockers() -> None:
    assert approval_blockers(ENTRY, {}, None) == []
    review = card(CardAction.ENTRY, qty=None, missing_fields=["underlying_confirmation"])
    assert approval_blockers(review, {}, None) == ["underlying_confirmation", "qty"]
    assert approval_blockers(review, {"underlying_confirmation": "MU", "qty": 2}, None) == []
    blank = card(CardAction.TRIM, labels=["provider_fraction:half"])
    assert approval_blockers(blank, {}, 1) == ["qty"]
    assert approval_blockers(blank, {"qty": 0}, 1) == ["qty"]
    assert approval_blockers(blank, {"qty": 2}, 1) == ["qty_exceeds_lot"]
    assert approval_blockers(blank, {"qty": 1}, 1) == []
    close = card(CardAction.CLOSE, qty=None)  # C105: a close takes no typed quantity
    assert approval_blockers(close, {}, 3) == []
    assert approval_blockers(close, {}, None) == ["no_open_lot"]
    disabled = card(CardAction.ENTRY, qty=None, disabled_reason="max exposure")
    assert approval_blockers(disabled, {"qty": 1}, None) == ["disabled"]
    be = card(CardAction.STOP, stop=StopProposal(level=None, basis="premium", held_by="broker"))
    assert approval_blockers(be, {}, 2) == ["stop_level"]
    assert approval_blockers(be, {"stop_level": D("1.50")}, 2) == []
    cancel = card(CardAction.STOP, stop=StopProposal(cancel=True))
    assert approval_blockers(cancel, {}, 2) == []
