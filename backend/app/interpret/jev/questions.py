"""TRD Appendix A as data. Wording is tunable here without code changes; option labels are
what `mapping.py` reads. Every Choice has a no-match option.

`VERSION` (F080) is a fingerprint, not a hand-edited label: the sha256 of the whole catalog
(ids, types, instructions, criteria; built over a fixed sample message so the per-message
options have a stable shape) plus `mapping.ROUTING_VERSION`. Any wording, option or routing
change gives a new version, stored with every Jev / Laya call (`model_calls.prompt_version`)."""

import hashlib
import json
import uuid
from typing import Any

from typesafe_sdk import Choice, Noul, Question, Score

from app.interpret.candidates import Candidates, find_candidates
from app.interpret.context import BookEntry, CurrentMessage, ProviderContext
from app.interpret.jev.mapping import ROUTING_VERSION
from app.interpret.models import MsgType

MESSAGE = "`message.text`"

MSG_TYPES: dict[str, str] = {
    "entry": "Opens a new option position (buy to open).",
    "add": "Adds to a position the provider already holds.",
    "trim": "Sells part of a held position.",
    "full_exit": "Closes a held position completely.",
    "soft_exit": "Suggests taking profits or getting out without a firm instruction.",
    "stop_set": "Sets a stop loss on a held position.",
    "stop_move": "Moves an existing stop loss.",
    "stop_cancel": "Removes a stop loss.",
    "tp_set": "Sets take-profit targets.",
    "tp_hit_notice": "Reports that a take-profit target was reached.",
    "correction": "Corrects an earlier message by the same author.",
    "watch": "Names a contract to watch, without entering.",
    "conditional": "An entry that only applies if a stated condition happens.",
    "commentary_pnl": "Reports profit or loss, or comments on a trade already made.",
    "stock_entry": "Buys shares of a stock.",
    "stock_exit": "Sells shares of a stock.",
    "out_of_scope_strategy": "A spread, short option, or other strategy that is not a long option.",
    "chatter": "Anything else: talk, jokes, questions, market views without an instruction.",
}
assert set(MSG_TYPES) == {t.value for t in MsgType} - {"unparsed"}  # noqa: S101 — catalog check

NUMBER_ROLES: dict[str, str] = {
    "strike": "The option strike price.",
    "entry_price": "The price paid or to pay for the option or stock.",
    "entry_price_range_low": "The low end of an entry price range.",
    "entry_price_range_high": "The high end of an entry price range.",
    "stop_level": "The stop loss level (option premium or underlying price).",
    "take_profit_level": "A take-profit target.",
    "underlying_level": "A price of the underlying stock or index that is not a stop or target.",
    "exit_fraction": "How much of the position to sell.",
    "share_or_contract_qty": "A number of contracts or shares.",
    "pnl_or_percent": "A profit, loss or percentage gain/loss.",
    "date_or_time": "Part of a date or time.",
    "other": "None of these.",
}
EXIT_FRACTIONS = ("all", "half", "third", "quarter", "other_stated", "not_stated")
EXIT_INTENT = [
    "No exit intent at all.",
    "A passing remark about maybe taking profits later.",
    "Suggests trimming or taking some profit, without a firm instruction.",
    "Clearly sells part of the position.",
    "Clearly exits the whole position.",
]


def _yes(instructions: str) -> Noul:
    return Noul(instructions=instructions)


def build_questions(ctx: ProviderContext, candidates: Candidates) -> dict[str, Question]:
    """One request answers everything (TRD §4.3): independent questions over the same state."""
    q: dict[str, Question] = {
        "msg_type": Choice(
            instructions=f"What does the provider's current message {MESSAGE} do?",
            criteria=MSG_TYPES,
        ),
        "signal_count": Choice(
            instructions=f"How many separate trade instructions does {MESSAGE} contain?",
            criteria={
                "none": "No trade instruction.",
                "one": "Exactly one instruction (one contract, one action).",
                "multiple": "Two or more instructions, e.g. exit one position and enter another.",
            },
        ),
        "instrument": Choice(
            instructions=f"What does {MESSAGE} trade?",
            criteria={"option": "Options.", "stock": "Shares.", "unclear": "Cannot tell."},
        ),
        "right": Choice(
            instructions=f"Is the option in {MESSAGE} a call or a put?",
            criteria={
                "call": "A call ('c', 'call', 'calls').",
                "put": "A put ('p', 'put', 'puts').",
                "not_stated": "The message does not say.",
            },
        ),
        "exit_fraction": Choice(
            instructions=f"How much of the position does {MESSAGE} sell?",
            criteria={f: None for f in EXIT_FRACTIONS},
        ),
        "stop_basis": Choice(
            instructions=f"Is the stop in {MESSAGE} an option price or an underlying price?",
            criteria={
                "premium": "The option's own price.",
                "underlying": "A price of the stock or index.",
                "not_applicable": "There is no stop.",
            },
        ),
        "candle_close": _yes(f"The stop in {MESSAGE} only triggers on a candle close."),
        "has_prose_condition": _yes(
            f"{MESSAGE} makes the trade depend on a condition in words (e.g. 'if it breaks 770')."
        ),
        "is_correction": _yes(f"{MESSAGE} corrects a mistake in an earlier message."),
        "breakeven_stop": _yes(f"{MESSAGE} moves the stop to the entry price (breakeven)."),
        "stop_removed": _yes(f"{MESSAGE} removes the stop loss."),
        "exit_intent": Score(
            instructions=f"How strongly does {MESSAGE} say to get out of the position?",
            criteria=EXIT_INTENT,
        ),
        "ticker": _pick(
            f"Which ticker in {MESSAGE} is the traded underlying?",
            candidates.tickers,
            "candidates.tickers",
            {"not_stated": "No ticker is stated."},
        ),
        "expiry": _pick(
            f"Which text in {MESSAGE} states the option expiry?",
            candidates.expiry_tokens,
            "candidates.expiry_tokens",
            {"not_stated": "No expiry is stated."},
        ),
        "links_to": Choice(
            instructions=f"Which position in `provider_book` does {MESSAGE} act on?",
            criteria={
                **{str(b.id): b.summary for b in ctx.link_candidates},
                "new_position": "A new position, not in the book.",
                "ambiguous": "More than one position could fit.",
            },
        ),
    }
    for n, c in enumerate(candidates.numbers):
        q[f"role_{c.id}"] = Choice(
            instructions=f"In {MESSAGE}, what is the number `candidates.numbers[{n}]` "
            f"(text {c.text!r} at position {c.start})?",
            criteria=NUMBER_ROLES,
        )
    return q


def _pick(instructions: str, found: list[Any], path: str, no_match: dict[str, str]) -> Choice:
    options = {c.id: f"`{path}[{n}]` {c.text!r} at position {c.start}" for n, c in enumerate(found)}
    return Choice(instructions=instructions, criteria={**options, **no_match})


def _fingerprint() -> str:
    """The catalog over a fixed sample (one candidate of each kind, one position)."""
    text = "spy 9/28 770c .95 half"
    sample = ProviderContext(
        now_et="2026-01-02 10:00:00 ET",
        message=CurrentMessage(
            id="1",
            author_id="1",
            at_et="2026-01-02 10:00:00 ET",
            text=text,
            event_type="create",
            channel_id="1",
            reply_to=None,
        ),
        reply_parent=None,
        recent_author_messages=[],
        provider_book=[BookEntry(id=uuid.UUID(int=1), summary="<position>")],
    )
    catalog = build_questions(sample, find_candidates(text))
    doc = {
        qid: q.model_dump(mode="json") if isinstance(q, Choice | Noul | Score) else dict(q)
        for qid, q in sorted(catalog.items())
    }
    raw = json.dumps({"questions": doc, "routing": ROUTING_VERSION}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


VERSION = f"jev_questions@{_fingerprint()}"
