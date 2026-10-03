"""Feed-only guard (F079): a model's "this is chatter" may not silence a message that could
act on a position the provider holds.

Jev read "gotta go here" (an exit) as chatter at 0.95 and "pop it here @everyone" at 0.83
(docs/reviews/jev-performance-2026-09-30.md). Confidence is not evidence: when the lane holds a
live position or today's setup and the text carries any sign of an instruction, the message is
not feed-only; it goes to the semantic fallback (the optional LLM) or to a person.

Pure. Over-blocking costs a review card; under-blocking can drop an exit.
"""

import re

from app.interpret.candidates import Candidates
from app.interpret.context import ProviderContext

PINGS = re.compile(r"@\w+|<@[!&]?\d+>")  # "@here", "@everyone", "<@123>": not words of the text
# Words a provider uses to act on a position (exit, trim, stop, re-entry, add, take profit).
ACTION = re.compile(
    r"\b(?:out|done|cut(?:ting)?|stop(?:s|ped|ping)?|sl|trim(?:s|med|ming)?|sold|sell(?:ing)?"
    r"|clos(?:e|ed|es|ing)|exit(?:s|ed|ing)?|take|taking|took|profits?|runners?|rest|half"
    r"|(?-i:BE)|b/e|break\s*even|scratch(?:ed)?|pop(?:ped)?|gone|lock(?:ed|ing)?|pay(?:ing)?\s+yourself"
    r"|paid|back\s+in|re-?ent(?:ry|er|ered)|reload(?:ed|ing)?|add(?:ed|ing)?|averag(?:e|ed|ing)"
    r"|flat|bail(?:ed)?|dump(?:ed)?|secur(?:e|ed|ing)|tp\d?|target)\b",
    re.IGNORECASE,
)
# "gotta go here", "get out now": leaving, said as an instruction about now.
LEAVING = re.compile(r"\b(?:go|gotta|going|get|leave|leaving)\b.*\b(?:here|now)\b", re.IGNORECASE)
_QUOTES = "'\"\u2018\u2019\u201c\u201d"  # straight and curly quotes
_QUOTED = re.compile(f"[{_QUOTES}]([^{_QUOTES}]{{1,30}})[{_QUOTES}]")
_FRACTION_WORDS = frozenset({"half", "third", "quarter", "most", "rest"})


def provider_terms(profile: dict[str, list[str]]) -> list[str]:
    """Quoted terms in the provider's lexicon ("'pop' means take profit" -> "pop")."""
    return [
        m.group(1).strip().lower()
        for item in profile.get("lexicon", [])
        for m in _QUOTED.finditer(item)
    ]


def feed_block(text: str, ctx: ProviderContext, candidates: Candidates) -> str | None:
    """Why a feed-only verdict may not stand, or None. Only with a non-empty lane book."""
    if not ctx.provider_book:
        return None  # nothing it could act on
    body = PINGS.sub(" ", text)
    if m := ACTION.search(body):
        return f"action word {m.group(0)!r}"
    if m := LEAVING.search(body):
        return f"leaving {m.group(0)!r}"
    lowered = body.lower()
    for term in provider_terms(ctx.provider_profile):
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered):
            return f"provider term {term!r}"
    for f in candidates.fraction_words:
        if f.text.endswith("%") or f.text.lower() in _FRACTION_WORDS or "/" in f.text:
            return f"fraction {f.text!r}"
    for n in candidates.numbers:
        if "." in n.text or n.text.lower().endswith("/con"):
            return f"price {n.text!r}"
    return None
