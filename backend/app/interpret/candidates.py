"""Candidate finders (TRD §4.1): code over-finds every ticker, number, expiry token and
fraction word with exact offsets, so models only *choose* among spans copied from the text and
never write a number themselves (C78). Pure; no symbol whitelist (C86).
"""

import re

from pydantic import BaseModel, ConfigDict


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # t1.., n1.., e1.., f1.. in text order (Jev role_<id> questions)
    text: str  # exactly text[start:end]
    start: int
    end: int


class Candidates(BaseModel):
    model_config = ConfigDict(frozen=True)

    tickers: list[Candidate]
    numbers: list[Candidate]
    expiry_tokens: list[Candidate]
    fraction_words: list[Candidate]
    numbers_dropped: int = 0  # F090: numbers past MAX_NUMBERS, not asked about


_WEEKDAY = r"mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?"
_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
EXPIRY = re.compile(
    rf"\b\d{{1,3}}\s?dte\b|\b(?:{_WEEKDAY})\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2}}(?:\d{{2}})?)?\b"
    rf"|\b(?:{_MONTH})\.?\s?\d{{1,2}}\b",
    re.IGNORECASE,
)
NUMBER = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:/con\b|[cp%k](?![a-z]))?",
    re.IGNORECASE,
)
TICKER = re.compile(r"(?<![A-Za-z0-9$])\$?[A-Za-z]{1,5}(?![A-Za-z0-9])")
FRACTION = re.compile(
    r"\b(?:half|third|quarter|all|rest|some|most)\b|\b1/[234]\b|(?<![\d.])\d+(?:\.\d+)?%",
    re.IGNORECASE,
)

# What a fraction word says (F071: an exit_fraction is literal when one of these is in the text).
FRACTION_VALUES = {
    "half": "half",
    "1/2": "half",
    "50%": "half",
    "third": "third",
    "1/3": "third",
    "33%": "third",
    "quarter": "quarter",
    "1/4": "quarter",
    "25%": "quarter",
    "all": "all",
    "rest": "all",
    "100%": "all",
}


def fraction_value(word: str) -> str | None:
    return FRACTION_VALUES.get(" ".join(word.lower().split()).removeprefix("a "))


# Common English / trading words that are not tickers. Over-finding is fine (models choose);
# this only keeps the candidate list short.
STOP_WORDS = frozenset(
    """
    a am an and are as at avg be bot buy by c call calls close con day dte down et exit exp
    for from go got had has have here hit i if in is it just k lets long lotto may me my new
    no not now of off ok on or out p pm put puts rest sell sl size so sold some still stop
    take tp that the this to took trim up us was we week went will with you all half most
    add adds adding entry open over same see than then they too under very watch
    """.split()
)


def find_candidates(text: str) -> Candidates:
    expiry = _spans(EXPIRY, text)
    numbers = [
        s for s in _spans(NUMBER, text) if not _inside(s, expiry) and not _is_label_digit(s, text)
    ]
    tickers = [
        s
        for s in _spans(TICKER, text)
        if s[2].lstrip("$").lower() not in STOP_WORDS and not _inside(s, expiry)
    ]
    return Candidates(
        tickers=_ids("t", tickers),
        numbers=_ids("n", numbers),
        expiry_tokens=_ids("e", expiry),
        fraction_words=_ids("f", _spans(FRACTION, text)),
    )


Span = tuple[int, int, str]


def _spans(pattern: re.Pattern[str], text: str) -> list[Span]:
    return [(m.start(), m.end(), m.group()) for m in pattern.finditer(text)]


def _is_label_digit(span: Span, text: str) -> bool:
    """The `1` of "TP1" / the `2` of "T2": a bare 1-2 digit integer glued to a letter names a
    level, it is not a quantity or a price. Offering it to the model sends every
    "Entry / SL / TP1 / TP2" signal to review, because the model is never sure what it is."""
    start, _, raw = span
    return start > 0 and text[start - 1].isalpha() and raw.isdigit() and len(raw) <= 2


def _inside(span: Span, others: list[Span]) -> bool:
    """Overlap rule: a number/word inside a (longer) expiry token belongs to the token."""
    return any(o[0] <= span[0] and span[1] <= o[1] for o in others)


def _ids(prefix: str, spans: list[Span]) -> list[Candidate]:
    return [
        Candidate(id=f"{prefix}{i}", text=t, start=s, end=e)
        for i, (s, e, t) in enumerate(sorted(spans), start=1)
    ]


# --- F090: what the models are asked about (report fix #2) -----------------------------------
# `find_candidates` stays an over-finder: the note guard and the resolver need every span. The
# interpreters get a cleaned copy with fewer `role_nN` questions that must all clear `t_field`.
# Ticker candidates are NOT filtered by a symbol list: on the F082 replay that cost feed-only
# settlements (Jev reads a cloud of word candidates as a sign of chatter) and gained nothing.

MAX_NUMBERS = 12  # ponytail: a constant; move to runtime config if real messages need more
_NOISE = re.compile(r"<[@#:a-z]+[^>]*>|https?://\S+|\b\d{1,2}:\d{2}(?:\s?[ap]m)?\b", re.I)


def clean_candidates(c: Candidates, text: str) -> Candidates:
    """No ticker or number inside a mention, emoji, URL or clock time. Past MAX_NUMBERS the
    numbers are dropped and counted; the mapper then never settles an actionable message on a
    partial reading."""
    noise = [(m.start(), m.end(), m.group()) for m in _NOISE.finditer(text)]
    numbers = [n for n in c.numbers if not _inside((n.start, n.end, n.text), noise)]
    return c.model_copy(
        update={
            "tickers": [t for t in c.tickers if not _inside((t.start, t.end, t.text), noise)],
            "numbers": numbers[:MAX_NUMBERS],
            "numbers_dropped": max(0, len(numbers) - MAX_NUMBERS),
        }
    )
