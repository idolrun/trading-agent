"""F072: watch / conditional setups and re-entries as provider-position state."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.interpret.fast import fast_parse
from app.interpret.models import LinkMethod, LinkOutcome
from app.interpret.signal import ContextSource, LinkHint, ResolvedSource
from app.ledger.correlation import LinkInputs, resolve_link
from app.ledger.models import PositionChange, PositionStatus
from app.ledger.positions import InvalidTransition, PositionState, apply_to_position
from app.ledger.resolver import Prior, Resolution
from app.validate.normalize import NormalizedSignal, Price

EXP = date(2026, 10, 2)


def sig(msg_type: str, **f: object) -> NormalizedSignal:
    return NormalizedSignal(msg_type=msg_type, instrument=f.pop("instrument", None), **f)  # type: ignore[arg-type]


def entry(underlying: str = "SPY", strike: str = "770", right: str = "call") -> NormalizedSignal:
    p = Decimal("1.20")
    return sig(
        "entry",
        instrument="option",
        underlying=underlying,
        expiry=EXP,
        strike=Decimal(strike),
        right=right,
        entry_price=Price(raw=p, value=p, scale="as_is"),
    )


WATCH_SPY = PositionState(instrument=None, underlying="SPY", status=PositionStatus.WATCH)


def test_watch_opens_a_setup_and_a_conditional_upgrades_it() -> None:
    watched = apply_to_position(None, sig("watch", underlying="SPY"))
    assert watched is not None and watched.change == PositionChange.WATCH
    assert (watched.after.status, watched.after.instrument) == (PositionStatus.WATCH, None)
    cond = apply_to_position(
        watched.after,
        sig("conditional", underlying="SPY", right="call", prose_condition="over 770"),
    )
    assert cond is not None and cond.change == PositionChange.CONDITION_SET
    assert (cond.after.status, cond.after.option_right, cond.after.condition_text) == (
        PositionStatus.CONDITIONAL,
        "call",
        "over 770",
    )


def test_a_setup_without_an_underlying_is_nothing() -> None:
    assert apply_to_position(None, sig("watch")) is None


def test_entering_a_setup_opens_it_in_place() -> None:
    cond = WATCH_SPY.model_copy(
        update={"status": PositionStatus.CONDITIONAL, "condition_text": "if it holds 770"}
    )
    opened = apply_to_position(cond, entry())
    assert opened is not None and opened.change == PositionChange.OPEN
    assert opened.before == cond
    assert (opened.after.status, opened.after.strike, opened.after.condition_text) == (
        PositionStatus.OPEN,
        Decimal(770),
        "if it holds 770",
    )


@pytest.mark.parametrize("msg_type", ["trim", "full_exit", "stop_move", "add"])
def test_a_setup_cannot_be_trimmed_stopped_or_added_to(msg_type: str) -> None:
    with pytest.raises(InvalidTransition):
        apply_to_position(WATCH_SPY, sig(msg_type, exit_fraction="half", breakeven_stop=True))


P_SETUP, P_OTHER, P_LIVE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def link(signal: NormalizedSignal, setups: dict[uuid.UUID, PositionState]) -> object:
    return resolve_link(
        signal,
        LinkHint(),
        LinkInputs(provider_id=7, book={}, setups=setups),
        t_link=Decimal("0.8"),
        hint_method=LinkMethod.JEV,
    )


def test_an_entry_links_to_the_one_setup_it_fits() -> None:
    d = link(entry(), {P_SETUP: WATCH_SPY})
    assert (d.outcome, d.position_id) == (LinkOutcome.LINKED, P_SETUP)  # type: ignore[attr-defined]


def test_an_entry_contradicting_the_setup_is_a_new_position() -> None:
    puts = WATCH_SPY.model_copy(update={"option_right": "put"})
    assert link(entry(), {P_SETUP: puts}).outcome == LinkOutcome.NEW_POSITION  # type: ignore[attr-defined]


def test_two_fitting_setups_is_a_new_position_not_a_guess() -> None:
    d = link(entry(), {P_SETUP: WATCH_SPY, P_OTHER: WATCH_SPY})
    assert d.outcome == LinkOutcome.NEW_POSITION  # type: ignore[attr-defined]


def test_a_restated_watch_links_its_setup() -> None:
    d = link(sig("conditional", underlying="SPY"), {P_SETUP: WATCH_SPY})
    assert (d.outcome, d.position_id) == (LinkOutcome.LINKED, P_SETUP)  # type: ignore[attr-defined]


def prior(position_id: uuid.UUID, status: str = "closed", **over: object) -> Prior:
    values: dict[str, object] = {
        "position_id": position_id,
        "status": status,
        "instrument": "option",
        "underlying": "SPY",
        "expiry": EXP,
        "strike": Decimal(765),
        "right": "put",
        "opened_message_id": 4242,
    }
    return Prior.model_validate(values | over)


def reentry(*priors: Prior) -> Resolution:
    return Resolution(outcome="none", reentry=priors)


def test_back_in_re_enters_the_one_position_closed_today() -> None:
    parsed = fast_parse("back in @ 2.10 @everyone", reentry(prior(P_LIVE)), [])
    assert parsed is not None
    [s] = parsed.signals
    f = s.fields
    assert (s.msg_type, f.underlying.value, f.strike.value, f.right.value) == (  # type: ignore[union-attr]
        "entry",
        "SPY",
        Decimal(765),
        "put",
    )
    assert f.expiry_token is not None and f.expiry_token.value == "10/2/2026"
    assert isinstance(f.strike.source, ContextSource)  # type: ignore[union-attr]
    assert f.strike.source.source_message_id == 4242  # type: ignore[union-attr]
    assert f.entry_price is not None and f.entry_price.value == Decimal("2.10")
    reentry_of = s.field_sources["reentry_of"]
    assert isinstance(reentry_of, ResolvedSource) and reentry_of.inputs["position_id"] == str(
        P_LIVE
    )


def test_back_in_with_two_candidates_or_an_incomplete_setup_is_not_settled_by_code() -> None:
    assert fast_parse("back in", reentry(prior(P_LIVE), prior(P_OTHER)), []) is None
    incomplete = prior(P_SETUP, status="watch", instrument=None, strike=None, right=None)
    assert fast_parse("back in", reentry(incomplete), []) is None
    assert fast_parse("back in", reentry(), []) is None


def test_back_in_on_a_complete_setup_opens_it_without_a_reentry_pointer() -> None:
    parsed = fast_parse("reloading", reentry(prior(P_SETUP, status="conditional")), [])
    assert parsed is not None and "reentry_of" not in parsed.signals[0].field_sources
