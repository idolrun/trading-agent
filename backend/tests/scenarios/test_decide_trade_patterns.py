"""F023: decide() on TRD §12 real-trade patterns (AC-14, AC-15, AC-16) and the tier rules."""

import uuid
from datetime import timedelta
from typing import Any

from app.cards.models import CardAction, CardType
from app.decide.engine import TIER_CANNOT_BUY, decide
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


def test_option_entry_sized_from_tier() -> None:
    card = entry()
    assert (card.action, card.card_type) == (CardAction.ENTRY, CardType.NORMAL)
    assert (card.limit_price, card.qty) == (D("1.75"), 5)  # floor(1000 / 175)
    assert card.valid_until == POSTED + timedelta(seconds=60)


def test_range_uses_top() -> None:
    card = entry(entry_price=None, entry_price_low=px("1.50"), entry_price_high=px("1.80"))
    assert card.limit_price == D("1.89")


def test_tier_cannot_buy_one_contract_is_disabled() -> None:
    card = entry(size_tier="lotto", entry_price=px("2.50"))
    assert card.card_type == CardType.DISABLED and card.disabled_reason == TIER_CANNOT_BUY
    assert card.qty is None


def test_bracket_legs_at_entry() -> None:
    tps = (px("2.10"), px("2.50"), px("3.00"))
    card = entry(stop_level=px("1.20"), tp_levels=tps)
    assert [leg.qty for leg in card.tp_legs] == [2, 2, 1]  # 5 contracts, 3 TPs
    assert card.stop is not None and (card.stop.level, card.stop.held_by) == (D("1.20"), "broker")
    two = entry(entry_price=px("3.50"), tp_levels=tps)  # 1000 / 368 -> 2 contracts
    assert two.qty == 2 and [leg.qty for leg in two.tp_legs] == [1, 1]  # AC-16


def test_underlying_stop_is_system_held() -> None:
    card = entry(stop_level=px("769"), stop_basis="underlying", candle_close=True)
    assert card.stop is not None
    assert (card.stop.basis, card.stop.held_by, card.stop.candle_close) == (
        "underlying",
        "system",
        True,
    )


def test_unknown_tick_or_tier_is_review() -> None:
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
    assert entry(size_tier="huge").review_reasons == ["unknown_size_tier:huge"]


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


def test_stock_entry_uses_stock_tier() -> None:
    [card] = run(
        "stock_entry", link=NEW, instrument="stock", underlying="GOOGL", entry_price=px("180.25")
    )
    assert (card.action, card.qty, card.limit_price) == (CardAction.ENTRY, 26, D("189.26"))


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
    assert card.action == CardAction.ADD and card.qty == 5
    assert run("entry", entry_price=px("1.67"), **base) == []  # OD-18: no lot -> feed only


def test_trim_half_on_one_lot_blank_with_label() -> None:
    [card] = run("trim", lot_qty=1, exit_fraction="half", instrument=None)
    assert (card.action, card.qty, card.labels) == (CardAction.TRIM, None, ["lot_too_small"])
    assert card.card_type == CardType.NORMAL  # AC-15: not review, just needs a qty
    assert card.valid_until == POSTED + timedelta(seconds=600)


def test_exit_quantities() -> None:
    assert run("trim", lot_qty=5, exit_fraction="half", instrument=None)[0].qty == 2
    assert run("trim", lot_qty=5, exit_fraction="not_stated", instrument=None)[0].qty is None
    [all_out] = run("trim", lot_qty=5, exit_fraction="all", instrument=None)
    assert (all_out.action, all_out.qty) == (CardAction.EXIT, 5)
    assert run("full_exit", lot_qty=3, instrument=None)[0].qty == 3
    [soft] = run("soft_exit", lot_qty=3, instrument=None)
    assert (soft.qty, soft.labels) == (None, ["suggestion"])


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
