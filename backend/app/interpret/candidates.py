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
    numbers = [s for s in _spans(NUMBER, text) if not _inside(s, expiry)]
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


def _inside(span: Span, others: list[Span]) -> bool:
    """Overlap rule: a number/word inside a (longer) expiry token belongs to the token."""
    return any(o[0] <= span[0] and span[1] <= o[1] for o in others)


def _ids(prefix: str, spans: list[Span]) -> list[Candidate]:
    return [
        Candidate(id=f"{prefix}{i}", text=t, start=s, end=e)
        for i, (s, e, t) in enumerate(sorted(spans), start=1)
    ]
