"""Deterministic fast path (F070): messages whose whole text is one obvious instruction become
`ParsedSignals` without a model.

A rule matches the ENTIRE message or not at all; a word it does not explain sends the message
to Jev. Every field is a literal span of the text (verified like any interpreter's, AC-11). A
follow-up rule fires only when the context resolver found exactly one position (F069): the fast
path never decides which position a message is about. Edits and deletes never take it.
"""

import re
from decimal import Decimal
from typing import Any

from app.interpret.candidates import EXPIRY, STOP_WORDS, fraction_value
from app.interpret.signal import (
    ContextSource,
    F,
    LinkHint,
    LiteralSpan,
    ParsedSignal,
    ParsedSignals,
    ResolvedSource,
    SignalFields,
)
from app.interpret.spans import parse_raw
from app.ledger.resolver import Resolution

_PRICE = r"\$?(?:\d*\.\d+|\d+(?:\.\d+)?)"
_DECIMAL_PRICE = r"\$?\d*\.\d+"  # an exit price must look like a premium, never a contract count
# Pings around the instruction ("... @everyone", "<@123> ..."): not part of what it says.
_PINGS = re.compile(r"^(?:\s*(?:@\w+|<@[!&]?\d+>))+\s*|\s*(?:(?:@\w+|<@[!&]?\d+>)\s*)+$")
_TICKER = r"\$?[a-z]{1,5}"
# In a follow-up a ticker must look like one ("SPY", "$spy"): "out more" is prose, not MORE.
_FOLLOW_UP_TICKER = r"(?-i:\$[A-Za-z]{1,5}|[A-Z]{1,5})"  # case-sensitive inside IGNORECASE
_EXP = EXPIRY.pattern

ENTRY = re.compile(
    rf"^(?:(?:bto|bought|buy|entry|in)\s+)?(?P<ticker>{_TICKER})\s+"
    rf"(?:(?P<exp1>{_EXP})\s+)?"
    r"(?P<strike>\d+(?:\.\d+)?\s?(?:c|p|calls?|puts?))\s+"
    rf"(?:(?P<exp2>{_EXP})\s+)?"
    rf"(?:@\s*)?(?P<price>{_PRICE})"
    rf"(?:\s+(?:sl|stop)\s+(?P<stop>{_PRICE}))?"
    rf"(?:\s+(?:tps?|targets?)\s+(?P<tps>(?:{_PRICE})(?:\s*/\s*(?:{_PRICE}))*))?"
    r"$",
    re.IGNORECASE,
)
_REST = r"all|rest|the\s+rest|runners|remaining|remainder"
EXIT = re.compile(  # "sold" alone could be part of the position: only with "all" / "rest" ...
    rf"^(?:(?:all\s+out|out|exit(?:ed)?|closed?)(?:\s+(?:{_REST}))?|sold\s+(?:{_REST}))"
    rf"(?:\s+(?P<ticker>{_FOLLOW_UP_TICKER}))?"
    rf"(?:\s+(?:here|now))?(?:\s+@?\s*(?:{_DECIMAL_PRICE}))?$",
    re.IGNORECASE,
)
TRIM = re.compile(
    rf"^(?:@?\s*(?:{_DECIMAL_PRICE})\s+)?"  # "1.63 trim": the price first
    r"(?:trim(?:med|ming)?|took|taking|sold|sell(?:ing)?)(?:\s+off)?"
    r"(?:\s+(?P<fraction>half|a\s+third|third|a\s+quarter|quarter|1/2|1/3|1/4|\d{1,3}%))?"
    rf"(?:\s+(?P<ticker>{_FOLLOW_UP_TICKER}))?"
    rf"(?:\s+(?:here|now))?(?:\s+@?\s*(?:{_DECIMAL_PRICE}))?$",
    re.IGNORECASE,
)
STOP_BE = re.compile(
    r"^(?:(?:moving|move|moved)\s+)?(?:stops?|sl)\s+(?:(?:to|at|@)\s+)?(?:be|b/e|breakeven|break\s+even)$"
    r"|^(?:be|breakeven)\s+stops?$",
    re.IGNORECASE,
)
REENTRY_MSG = re.compile(  # F072: the whole message is "back in [here] [@ price]"
    r"^(?:back\s+in|re-?entry|re-?enter(?:ing|ed)?|in\s+again|reload(?:ed|ing)?)"
    rf"(?:\s+(?:here|now))?(?:\s+@?\s*(?P<price>{_PRICE}))?$",
    re.IGNORECASE,
)
# "Watching NVDA 200C, might get in if it holds 198": a heads-up, never an order
WATCH = re.compile(
    r"^(?:watching|eyeing|eyeballing|looking\s+at|keeping\s+an\s+eye\s+on)"
    rf"\s+(?P<ticker>{_TICKER})\b",
    re.IGNORECASE,
)
# A word that makes a watch message an instruction (or a premium) goes to Jev, never a feed.
_WATCH_ACTION = re.compile(
    r"\b(?:bought|buy(?:ing)?|bto|sold|sell(?:ing)?|trim(?:med)?|took|stops?|sl|tps?|"
    r"out|exit(?:ed)?|closed?)\b|@|\d*\.\d+",
    re.IGNORECASE,
)
_TRIM_VERB_ONLY = re.compile(r"^(?:took|taking|sold|sell)", re.IGNORECASE)  # need a fraction


def fast_parse(text: str, resolution: Resolution | None) -> ParsedSignals | None:
    """None: not obvious enough; Jev (and its fallbacks) decide."""
    stripped = _PINGS.sub("", text.strip())
    if not stripped or "\n" in stripped:
        return None
    offset = text.index(stripped)
    for rule in (_entry, _reentry, _trim_or_exit, _stop_breakeven, _watch):
        signal = rule(stripped, offset, resolution)
        if signal is not None:
            return ParsedSignals(signals=[signal], interpreter="fast")
    return None


def _span(m: re.Match[str], group: str, offset: int) -> LiteralSpan:
    start, end = m.span(group)
    return LiteralSpan(raw=m.group(group), start=start + offset, end=end + offset)


def _lit(m: re.Match[str], group: str, offset: int, field: str) -> F[Any] | None:
    if m.group(group) is None:
        return None
    value = parse_raw(field, m.group(group))
    return None if value is None else F(value=value, source=_span(m, group, offset))


def _entry(text: str, offset: int, _r: Resolution | None) -> ParsedSignal | None:
    m = ENTRY.match(text)
    if m is None or m.group("ticker").lstrip("$").lower() in STOP_WORDS:
        return None
    if m.group("exp1") and m.group("exp2"):
        return None
    strike, right = _lit(m, "strike", offset, "strike"), _lit(m, "strike", offset, "right")
    price = _lit(m, "price", offset, "entry_price")
    if strike is None or right is None or price is None:
        return None
    exp_group = "exp1" if m.group("exp1") else "exp2" if m.group("exp2") else None
    tps: list[F[Decimal]] = []
    if m.group("tps"):
        base = m.start("tps")
        for t in re.finditer(_PRICE, m.group("tps")):
            raw = t.group()
            start = offset + base + t.start()
            value = parse_raw("tp_levels", raw)  # "$2.90" -> 2.90
            if not isinstance(value, Decimal):
                return None
            tps.append(
                F(
                    value=value,
                    source=LiteralSpan(raw=raw, start=start, end=start + len(raw)),
                )
            )
    fields = SignalFields(
        underlying=_lit(m, "ticker", offset, "underlying"),
        expiry_token=_lit(m, exp_group, offset, "expiry_token") if exp_group else None,
        strike=strike,
        right=right,
        entry_price=price,
        stop_level=_lit(m, "stop", offset, "stop_level"),
        tp_levels=tps,
    )
    return ParsedSignal(
        msg_type="entry",
        instrument="option",
        fields=fields,
        link_hint=LinkHint(target="new_position", confidence=Decimal(1)),
        field_sources={"msg_type": _rule("option_entry"), "instrument": _span(m, "strike", offset)},
    )


def _reentry(text: str, offset: int, r: Resolution | None) -> ParsedSignal | None:
    """F072: "back in" re-enters the lane's one position closed today (or today's one setup).
    The contract comes from that position, cited as context (its opening message), so the card
    is marked inferred; the price, if any, is the text's own. Several candidates: Jev/review."""
    m = REENTRY_MSG.match(text)
    if m is None or r is None or len(r.reentry) != 1:
        return None
    prior = r.reentry[0]
    if not prior.complete:
        return None  # a setup that never named its contract: nothing to re-enter by code
    cited: ContextSource | ResolvedSource = (
        ContextSource(source_message_id=prior.opened_message_id)
        if prior.opened_message_id is not None
        else ResolvedSource(rule="reentry", inputs={"position_id": str(prior.position_id)})
    )
    fields: dict[str, Any] = {"underlying": F(value=prior.underlying, source=cited)}
    if prior.instrument == "option":
        assert prior.expiry and prior.strike and prior.right  # noqa: S101 — complete
        token = f"{prior.expiry.month}/{prior.expiry.day}/{prior.expiry.year}"
        fields |= {
            "expiry_token": F(value=token, source=cited),
            "strike": F(value=prior.strike, source=cited),
            "right": F(value=prior.right, source=cited),
        }
    if (price := _lit(m, "price", offset, "entry_price")) is not None:
        fields["entry_price"] = price
    sources: dict[str, Any] = {"msg_type": _rule("reentry")}
    if prior.status == "closed":  # a setup is opened in place; a closed position is re-entered
        sources["reentry_of"] = ResolvedSource(
            rule="reentry", inputs={"position_id": str(prior.position_id)}
        )
    return ParsedSignal(
        msg_type="stock_entry" if prior.instrument == "stock" else "entry",
        instrument=prior.instrument,
        fields=SignalFields(**fields),
        link_hint=LinkHint(target="new_position", confidence=Decimal(1)),
        field_sources=sources,
    )


def _unique(r: Resolution | None) -> LinkHint | None:
    if r is None or r.outcome != "unique" or r.position_id is None:
        return None
    return LinkHint(target=r.position_id, confidence=Decimal(1))


def _trim_or_exit(text: str, offset: int, r: Resolution | None) -> ParsedSignal | None:
    hint = _unique(r)
    if hint is None:
        return None
    if (m := EXIT.match(text)) is not None and _ticker_names_a_position(m, r):
        return ParsedSignal(
            msg_type="full_exit",
            fields=SignalFields(
                underlying=_lit(m, "ticker", offset, "underlying"), exit_fraction="all"
            ),
            link_hint=hint,
            field_sources={"msg_type": _rule("exit"), "exit_fraction": _rule("exit")},
        )
    m = TRIM.match(text)
    if m is None or not _ticker_names_a_position(m, r):
        return None
    raw_fraction = m.group("fraction")
    if raw_fraction is None and _TRIM_VERB_ONLY.match(text):
        return None  # "sold" alone: all or part? Jev decides
    fraction = (
        "not_stated" if raw_fraction is None else fraction_value(raw_fraction) or "other_stated"
    )
    return ParsedSignal(
        msg_type="trim",
        fields=SignalFields(
            underlying=_lit(m, "ticker", offset, "underlying"), exit_fraction=fraction
        ),
        link_hint=hint,
        field_sources={
            "msg_type": _rule("trim"),
            "exit_fraction": _span(m, "fraction", offset) if raw_fraction else _rule("trim"),
        },
    )


def _stop_breakeven(text: str, _offset: int, r: Resolution | None) -> ParsedSignal | None:
    hint = _unique(r)
    if hint is None or STOP_BE.match(text) is None:
        return None
    return ParsedSignal(
        msg_type="stop_move",
        fields=SignalFields(breakeven_stop=True),
        link_hint=hint,
        field_sources={
            "msg_type": _rule("stop_breakeven"),
            "breakeven_stop": _rule("stop_breakeven"),
        },
    )


def _ticker_names_a_position(m: re.Match[str], r: Resolution | None) -> bool:
    """A follow-up's trailing ticker-shaped word counts only when the resolver matched it to a
    lane position ("out SPY" with SPY held). Anything else ("all out HAGW", "LFG", a ticker the
    lane does not hold) is a word the rule cannot explain: Jev decides, never a literal
    underlying read off prose."""
    ticker = m.group("ticker")
    if ticker is None:
        return True
    word = ticker.lstrip("$").upper()
    if word.lower() in STOP_WORDS or r is None:
        return False
    return word in {str(t).upper() for t in r.evidence.get("tickers", [])}


def _watch(text: str, offset: int, _r: Resolution | None) -> ParsedSignal | None:
    m = WATCH.match(text)
    if m is None or m.group("ticker").lstrip("$").lower() in STOP_WORDS:
        return None
    if _WATCH_ACTION.search(text):
        return None
    return ParsedSignal(
        msg_type="watch",
        fields=SignalFields(underlying=_lit(m, "ticker", offset, "underlying")),
        field_sources={"msg_type": _rule("watch")},
    )


def _rule(name: str) -> ResolvedSource:
    """Provenance of what a fast-path rule decided (F071): code, not a model, not a span."""
    return ResolvedSource(rule=f"fast:{name}")
