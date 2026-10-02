"""F070: the deterministic fast path matches whole obvious messages only, with literal spans,
and never picks a position itself."""

import uuid
from decimal import Decimal

import pytest

from app.interpret.fast import fast_parse
from app.interpret.spans import verify_literal_spans
from app.ledger.resolver import Resolution

P = uuid.uuid4()
UNIQUE = Resolution(outcome="unique", position_id=P, candidates=(P,), method="reply")
AMBIGUOUS = Resolution(outcome="ambiguous", candidates=(P, uuid.uuid4()))
TIERS = ["default", "lotto", "small"]


def parse(text: str, r: Resolution | None = None) -> dict[str, object] | None:
    parsed = fast_parse(text, r, TIERS)
    if parsed is None:
        return None
    assert parsed.interpreter == "fast"
    assert verify_literal_spans(parsed, text) == []  # every value is the text's own span
    [s] = parsed.signals
    f = s.fields
    out: dict[str, object] = {"type": s.msg_type}
    for name in ("underlying", "expiry_token", "strike", "right", "entry_price", "stop_level"):
        if (v := getattr(f, name)) is not None:
            out[name] = v.value
    if f.tp_levels:
        out["tps"] = [t.value for t in f.tp_levels]
    if f.size_tier:
        out["tier"] = f.size_tier.value
    if f.exit_fraction:
        out["fraction"] = f.exit_fraction
    if f.breakeven_stop:
        out["breakeven"] = True
    if isinstance(s.link_hint.target, uuid.UUID):
        out["target"] = s.link_hint.target
    return out


def test_option_entries() -> None:
    assert parse("SPY 765P .54") == {
        "type": "entry",
        "underlying": "spy",
        "strike": Decimal(765),
        "right": "put",
        "entry_price": Decimal(".54"),
    }
    assert parse("SPY 9/28 765p @ 1.20 sl .80 tp 1.50/2.00") == {
        "type": "entry",
        "underlying": "spy",
        "expiry_token": "9/28",
        "strike": Decimal(765),
        "right": "put",
        "entry_price": Decimal("1.20"),
        "stop_level": Decimal(".80"),
        "tps": [Decimal("1.50"), Decimal("2.00")],
    }
    assert parse("$QQQ 745 puts 1.36 lotto") == {
        "type": "entry",
        "underlying": "qqq",
        "strike": Decimal(745),
        "right": "put",
        "entry_price": Decimal("1.36"),
        "tier": "lotto",
    }


@pytest.mark.parametrize(
    "text",
    [
        "SPY 765P .54 nice",  # an unexplained word
        "SPY 765P .54 yolo",  # not a configured tier
        "buy 765p .54",  # no ticker
        "spy 770c .95 lotto, sl 769 spy",  # more than one clause
        "if SPY holds 770 then calls",  # a condition
        "watching SPY 765P",
        "SPY 765P .54\nQQQ 745P .60",  # two lines
    ],
)
def test_anything_less_obvious_goes_to_jev(text: str) -> None:
    assert parse(text) is None


def test_follow_ups_need_a_unique_resolution() -> None:
    assert parse("trim half", UNIQUE) == {"type": "trim", "fraction": "half", "target": P}
    assert parse("trim half", AMBIGUOUS) is None
    assert parse("trim half", None) is None
    assert parse("stop breakeven", AMBIGUOUS) is None


def test_follow_up_rules() -> None:
    assert parse("out runners", UNIQUE) == {"type": "full_exit", "fraction": "all", "target": P}
    assert parse("out SPY", UNIQUE) == {
        "type": "full_exit",
        "underlying": "spy",
        "fraction": "all",
        "target": P,
    }
    assert parse("trim .72", UNIQUE) == {"type": "trim", "fraction": "not_stated", "target": P}
    assert parse("sold 1/3", UNIQUE) == {"type": "trim", "fraction": "third", "target": P}
    assert parse("trim 30%", UNIQUE) == {"type": "trim", "fraction": "other_stated", "target": P}
    assert parse("sl to be", UNIQUE) == {"type": "stop_move", "breakeven": True, "target": P}
    assert parse("moving stops to breakeven", UNIQUE) == {
        "type": "stop_move",
        "breakeven": True,
        "target": P,
    }


@pytest.mark.parametrize("text", ["sold", "out most", "out 2", "nice trim here", "back in"])
def test_unclear_follow_ups_go_to_jev(text: str) -> None:
    assert parse(text, UNIQUE) is None


def test_fast_path_provenance_is_code_or_the_text() -> None:
    """F071: a fast-path type is a code rule; a stated fraction is a literal span."""
    from app.interpret.signal import LiteralSpan, ResolvedSource

    parsed = fast_parse("trim half", UNIQUE, TIERS)
    assert parsed is not None
    src = parsed.signals[0].field_sources
    assert isinstance(src["msg_type"], ResolvedSource) and src["msg_type"].rule == "fast:trim"
    assert isinstance(src["exit_fraction"], LiteralSpan) and src["exit_fraction"].raw == "half"
    entry = fast_parse("SPY 765P .54", None, TIERS)
    assert entry is not None
    instrument = entry.signals[0].field_sources["instrument"]
    assert isinstance(instrument, LiteralSpan) and instrument.raw == "765P"


def test_real_message_shapes() -> None:
    """Shapes from the provider export: trailing pings, $ prices, spaced TP lists."""
    assert parse("$SPY 765P @ 1.42 @everyone") == {
        "type": "entry",
        "underlying": "spy",
        "strike": Decimal(765),
        "right": "put",
        "entry_price": Decimal("1.42"),
    }
    mstr = parse("MSTR 10/2 150 PUTS 2.45 SL 1.90 TP 2.90 / 3.3 / 3.8  @chimera118")
    assert mstr is not None and mstr["tps"] == [Decimal("2.90"), Decimal("3.3"), Decimal("3.8")]
    assert parse("INTC 2DTE 120 CALLS .95 @everyone") is not None
    assert parse("trim $2.90 @everyone", UNIQUE) == {
        "type": "trim",
        "fraction": "not_stated",
        "target": P,
    }
    assert parse("1.63 trim @everyone", UNIQUE) is not None
    assert parse("all out HAGW <@123456>", UNIQUE) == {
        "type": "full_exit",
        "underlying": "hagw",
        "fraction": "all",
        "target": P,
    }


@pytest.mark.parametrize(
    "text",
    [
        "out more 1.48 @everyone",  # "more" is prose, not a ticker
        "these just shot up 30% got the trim @everyone",
        "Unlimited trims on XLU, now 20% gains @everyone",
    ],
)
def test_real_prose_is_not_fast(text: str) -> None:
    assert parse(text, UNIQUE) is None
