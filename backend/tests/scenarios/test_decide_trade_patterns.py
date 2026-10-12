"""F023/F085: decide() on TRD §12 real-trade patterns (AC-14); the user types every quantity."""

import uuid
from datetime import timedelta
from typing import Any

from app.cards.models import CardAction, CardType
from app.decide.engine import decide
from app.decide.types import DecisionInputs, LotView
from app.interpret.models import LinkMethod, LinkOutcome
from app.interpret.signal import F, ParsedSignal, SignalFields
from app.ledger.correlation import LinkDecision
from app.ledger.positions import PositionState
from app.validate.normalize import NormalizedSignal, Price
from app.validate.service import ValidationResult
from tests.decide_helpers import (
    CONFIG,
    EXP,
    LINKED,
    LOT,
    NEW,
    P_ID,
    POSTED,
    D,
    entry,
    px,
    run,
)


def test_option_entry_has_price_but_no_quantity() -> None:
    card = entry()
    assert (card.action, card.card_type) == (CardAction.ENTRY, CardType.NORMAL)
    assert (card.limit_price, card.qty) == (D("1.75"), None)  # C105: the user enters it
    assert card.valid_until == POSTED + timedelta(seconds=60)


def test_signal_stated_quantity_is_ignored() -> None:
    card = entry(qty_stated=5)
    assert card.qty is None and card.disabled_reason is None and card.review_reasons == []


def test_range_uses_top() -> None:
    card = entry(entry_price=None, entry_price_low=px("1.50"), entry_price_high=px("1.80"))
    assert card.limit_price == D("1.89")


def test_bracket_at_entry_keeps_levels_until_the_user_enters_a_quantity() -> None:
    tps = (px("2.10"), px("2.50"), px("3.00"))
    card = entry(stop_level=px("1.20"), tp_levels=tps)
    assert card.tp_levels == [D("2.10"), D("2.50"), D("3.00")] and card.tp_legs == []
    assert card.stop is not None and (card.stop.level, card.stop.held_by) == (D("1.20"), "broker")


def test_underlying_stop_is_system_held() -> None:
    card = entry(stop_level=px("769"), stop_basis="underlying", candle_close=True)
    assert card.stop is not None
    assert (card.stop.basis, card.stop.held_by, card.stop.candle_close) == (
        "underlying",
        "system",
        True,
    )


def test_unknown_tick_is_review() -> None:
    [card] = run(
        "entry",
        link=NEW,
        tick=None,
        underlying="GOOGL",
        expiry=EXP,
        strike=D("357.5"),
        right="call",
        entry_price=px("1.67"),
    )
    assert card.card_type == CardType.REVIEW and card.review_reasons == ["tick_unknown"]


def test_unscaled_premium_is_review() -> None:
    card = entry(entry_price=Price(raw=D("167"), value=D("167")))
    assert card.card_type == CardType.REVIEW and "price_scale_undecided" in card.review_reasons


def test_missing_underlying_review_with_suggestion() -> None:
    [card] = run(
        "entry",
        link=None,
        validation={
            "status": "review",
            "reasons": ["underlying_missing"],
            "suggested_underlying": "MU",
        },
        expiry=EXP,
        strike=D("1000"),
        right="call",
        entry_price=px("12"),
    )
    assert card.card_type == CardType.REVIEW and card.suggested_underlying == "MU"
    assert card.missing_fields == ["underlying_confirmation"]  # AC-14
    assert card.qty is None


def test_stock_entry_has_limit_but_no_quantity() -> None:
    [card] = run(
        "stock_entry", link=NEW, instrument="stock", underlying="GOOGL", entry_price=px("180.25")
    )
    assert (card.action, card.qty, card.limit_price) == (CardAction.ENTRY, None, D("189.26"))


def test_stock_entry_without_price_not_actionable() -> None:
    assert run("stock_entry", link=NEW, validation={"status": "not_actionable"}) == []


def test_entry_on_open_position_is_add_only_with_lot() -> None:
    base: dict[str, Any] = {
        "underlying": "GOOGL",
        "expiry": EXP,
        "strike": D("357.5"),
        "right": "call",
    }
    [card] = run("entry", lot_qty=2, entry_price=px("1.67"), **base)
    assert card.action == CardAction.ADD and card.qty is None
    assert run("entry", entry_price=px("1.67"), **base) == []  # OD-18: no lot -> feed only


def test_trim_never_prefills_a_quantity() -> None:
    [card] = run("trim", lot_qty=1, exit_fraction="half", instrument=None)
    assert (card.action, card.qty) == (CardAction.TRIM, None)
    assert card.labels == ["provider_fraction:half"]  # information only
    assert card.card_type == CardType.NORMAL  # not review, just needs a qty (AC-15)
    assert card.valid_until == POSTED + timedelta(seconds=600)
    assert run("trim", lot_qty=5, exit_fraction="half", instrument=None)[0].qty is None
    assert run("trim", lot_qty=5, exit_fraction="not_stated", instrument=None)[0].qty is None


def test_full_exit_is_a_close_card_holding_the_lot() -> None:
    for t, kw in (("full_exit", {}), ("trim", {"exit_fraction": "all"})):
        [close] = run(t, lot_qty=5, instrument=None, **kw)
        assert (close.action, close.qty) == (CardAction.CLOSE, 5)
    [soft] = run("soft_exit", lot_qty=3, instrument=None)
    assert (soft.action, soft.qty, soft.labels) == (CardAction.EXIT, None, ["suggestion"])


def test_follow_up_without_lot_is_feed_only() -> None:
    assert run("trim", exit_fraction="half", instrument=None) == []
    assert run("stop_move", stop_level=px("1.30"), instrument=None) == []


def test_ambiguous_follow_up_is_review_with_candidates() -> None:
    other = uuid.uuid4()
    link = LinkDecision(
        LinkOutcome.AMBIGUOUS, LinkMethod.JEV, D("0.6"), candidate_position_ids=(P_ID, other)
    )
    [card] = run("trim", link=link, exit_fraction="half", instrument=None)
    assert card.card_type == CardType.REVIEW and card.candidate_position_ids == [P_ID, other]
    assert card.review_reasons == ["link_ambiguous"] and card.qty is None


def test_stop_cards() -> None:
    [move] = run("stop_move", lot_qty=2, stop_level=px("1.30"), instrument=None)
    assert move.action == CardAction.STOP and move.stop is not None and move.stop.level == D("1.3")
    [cancel] = run("stop_cancel", lot_qty=2, instrument=None)
    assert cancel.stop is not None and cancel.stop.cancel
    [be] = run("stop_move", lot_qty=2, breakeven_stop=True, instrument=None)
    assert be.stop is not None and be.stop.level is None
    assert be.breakeven_options is not None
    assert (be.breakeven_options.user_avg, be.breakeven_options.provider_entry) == (
        D("1.50"),
        D("1.67"),
    )
    [nolevel] = run("stop_set", lot_qty=2, instrument=None)
    assert nolevel.card_type == CardType.REVIEW and nolevel.missing_fields == ["stop_level"]


def test_tp_card_legs_over_lot() -> None:
    [card] = run("tp_set", lot_qty=3, tp_levels=(px("2.10"), px("2.50")), instrument=None)
    assert card.action == CardAction.TP and [leg.qty for leg in card.tp_legs] == [2, 1]


def test_feed_only_types() -> None:
    for t in ("watch", "commentary_pnl", "chatter", "conditional", "tp_hit_notice", "correction"):
        assert run(t, lot_qty=2, instrument=None) == []


def test_context_field_is_inferred_and_confidence_only_lowers() -> None:
    ctx = SignalFields(
        underlying=F[str](value="GOOGL", source={"kind": "context", "source_message_id": 1})
    )
    base: dict[str, Any] = {
        "underlying": "GOOGL",
        "expiry": EXP,
        "strike": D("357.5"),
        "right": "call",
        "entry_price": px("1.67"),
    }
    [inferred] = run("entry", link=NEW, fields=ctx, **base)
    assert inferred.card_type == CardType.INFERRED
    [low] = run("entry", link=NEW, field_confidence={"strike": D("0.5")}, **base)
    assert low.card_type == CardType.REVIEW
    [high] = run(
        "entry",
        link=None,
        field_confidence={"strike": D("0.99")},
        validation={"status": "review", "reasons": ["contract_incomplete"]},
        **base,
    )
    assert high.card_type == CardType.REVIEW  # high confidence never upgrades review


def test_deterministic() -> None:
    assert entry(tp_levels=(px("2.10"),)) == entry(tp_levels=(px("2.10"),))


def test_stock_stop_is_broker_held_on_price() -> None:
    stock = PositionState(instrument="stock", underlying="GOOGL")
    [card] = decide(
        DecisionInputs(
            signal=ParsedSignal(msg_type="stop_set"),
            validation=ValidationResult(
                status="ok",
                normalized=NormalizedSignal(
                    msg_type="stop_set",
                    instrument=None,
                    stop_level=px("170"),
                    stop_basis="underlying",
                ),
            ),
            link=LINKED,
            position=stock,
            lot=LotView(LOT, 10, D("180")),
            config=CONFIG,
            tick=None,
            posted_at=POSTED,
        )
    )
    assert card.stop is not None
    assert (card.stop.basis, card.stop.held_by) == ("premium", "broker")  # C61
