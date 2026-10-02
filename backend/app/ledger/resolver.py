"""Signal context resolver (F069): which of the lane's live provider positions a message can be
about, decided by code before any model is asked. Pure; the DB reads are
`ledger.repo.load_resolution`.

Order (strongest evidence first; a model never overrides it):
1. reply chain: the nearest ancestor (depth <= REPLY_DEPTH) whose signals of this provider link
   to exactly one live lane position;
2. thread: the live lane positions this provider's earlier messages in the same thread opened
   or acted on; exactly one wins;
3. contract: tickers / strikes / call-put stated in the message narrow the lane's book;
   exactly one left wins.
Otherwise AMBIGUOUS with the narrowed candidates (even a single one: one open position is not
evidence, PRD §10), or NONE when the lane's book is empty. Evidence that contradicts a reply or
thread pick (the message names another of the lane's underlyings) makes it AMBIGUOUS.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.interpret.candidates import Candidates
from app.interpret.models import LinkMethod
from app.ledger.positions import PositionState

REPLY_DEPTH = 3
# F072: words that re-open something the provider had ("back in", "re-entry", "reloaded")
REENTRY = re.compile(
    r"\b(?:back\s+in|re-?ent(?:ry|er|ered|ering)|in\s+again|reload(?:ed|ing)?)\b", re.IGNORECASE
)
_RIGHT_WORD = re.compile(r"\b(calls?|puts?)\b", re.IGNORECASE)
_STRIKE = re.compile(r"^(\d+(?:\.\d+)?)([cp])?$", re.IGNORECASE)

Outcome = Literal["unique", "ambiguous", "none"]


@dataclass(frozen=True)
class ParentLink:
    """A current link of one of a reply ancestor's signals (any author)."""

    provider_id: int
    position_id: uuid.UUID


class Prior(BaseModel):
    """F072: a lane position closed today, or today's setup: what "back in" can re-enter."""

    model_config = ConfigDict(frozen=True)

    position_id: uuid.UUID
    status: str
    instrument: Literal["option", "stock"] | None
    underlying: str
    expiry: date | None = None
    strike: Decimal | None = None
    right: Literal["call", "put"] | None = None
    opened_message_id: int | None = None  # the message that opened it (the card cites it)

    @property
    def complete(self) -> bool:
        if self.instrument == "stock":
            return True
        return self.instrument == "option" and None not in (self.expiry, self.strike, self.right)


class Resolution(BaseModel):
    """JSON-safe (graph state); `candidates` are always lane positions, in book order."""

    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    position_id: uuid.UUID | None = None
    candidates: tuple[uuid.UUID, ...] = ()
    method: LinkMethod | None = None  # reply | thread | contract | user_correction
    evidence: dict[str, Any] = {}
    reentry: tuple[Prior, ...] = ()  # F072: filled only when the text says "back in" & co.

    @classmethod
    def chosen(cls, position_id: uuid.UUID) -> "Resolution":
        """The user picked the position (review card, manual link, re-link approval)."""
        return cls(
            outcome="unique",
            position_id=position_id,
            candidates=(position_id,),
            method=LinkMethod.USER_CORRECTION,
            evidence={"rule": "user_choice"},
        )

    @classmethod
    def unresolved(cls, book: Mapping[uuid.UUID, Any]) -> "Resolution":
        """No evidence looked at (replay, corrections re-planning an opening)."""
        return cls(outcome="ambiguous" if book else "none", candidates=tuple(book))


class Evidence(BaseModel):
    """What the message itself states about a contract (code-found spans only)."""

    model_config = ConfigDict(frozen=True)

    tickers: frozenset[str] = frozenset()
    strikes: frozenset[Decimal] = frozenset()  # numbers, with or without a c/p suffix
    suffixed_strikes: frozenset[Decimal] = frozenset()  # "765p": a strike by its shape
    rights: frozenset[str] = frozenset()  # call | put

    @classmethod
    def of(cls, text: str, candidates: Candidates) -> "Evidence":
        tickers = {c.text.lstrip("$").upper() for c in candidates.tickers}
        strikes: set[Decimal] = set()
        suffixed: set[Decimal] = set()
        rights: set[str] = set()
        for c in candidates.numbers:
            m = _STRIKE.match(c.text.replace(",", ""))
            if m is None:
                continue  # "50%", "/con" prices: never a strike
            try:
                value = Decimal(m.group(1))
            except InvalidOperation:
                continue
            strikes.add(value)
            if m.group(2):
                suffixed.add(value)
                rights.add("call" if m.group(2).lower() == "c" else "put")
        for m in _RIGHT_WORD.finditer(text):
            rights.add("call" if m.group(1).lower().startswith("call") else "put")
        return cls(
            tickers=frozenset(tickers),
            strikes=frozenset(strikes),
            suffixed_strikes=frozenset(suffixed),
            rights=frozenset(rights),
        )


def resolve_context(
    *,
    provider_id: int,
    book: Mapping[uuid.UUID, PositionState],
    reply_hops: Sequence[Sequence[ParentLink]],
    thread_positions: frozenset[uuid.UUID],
    evidence: Evidence,
) -> Resolution:
    """`book`: the lane's live (open/trimmed) positions, in order. `reply_hops[i]`: the links of
    the i-th reply ancestor (any author; only this provider's count)."""
    if not book:
        return Resolution(outcome="none", evidence={"rule": "empty_book"})
    narrowed, contract = _by_contract(book, evidence)

    for depth, hop in enumerate(reply_hops, start=1):
        linked = list(
            dict.fromkeys(
                link.position_id
                for link in hop
                if link.provider_id == provider_id and link.position_id in book
            )
        )
        if len(linked) == 1:
            return _strong(linked[0], LinkMethod.REPLY, {"rule": "reply", "depth": depth}, narrowed)
        if linked:
            narrowed = tuple(p for p in narrowed if p in linked) or tuple(linked)
            break  # the nearest linked ancestor decides; farther ones are older context

    in_thread = [p for p in book if p in thread_positions]
    if len(in_thread) == 1:
        return _strong(in_thread[0], LinkMethod.THREAD, {"rule": "thread"}, narrowed)
    if in_thread:
        narrowed = tuple(p for p in narrowed if p in in_thread) or tuple(in_thread)

    named = "tickers" in contract or "strikes" in contract  # "calls" alone only narrows
    if named and len(narrowed) == 1:
        return Resolution(
            outcome="unique",
            position_id=narrowed[0],
            candidates=narrowed,
            method=LinkMethod.CONTRACT,
            evidence={"rule": "contract", **contract},
        )
    return Resolution(
        outcome="ambiguous",
        candidates=narrowed,
        evidence={"rule": "candidates", **contract},
    )


def _strong(
    position_id: uuid.UUID,
    method: LinkMethod,
    evidence: dict[str, Any],
    narrowed: tuple[uuid.UUID, ...],
) -> Resolution:
    """A reply / thread pick, unless the message's own contract words point elsewhere."""
    if position_id in narrowed:
        return Resolution(
            outcome="unique",
            position_id=position_id,
            candidates=(position_id,),
            method=method,
            evidence=evidence,
        )
    return Resolution(
        outcome="ambiguous",
        candidates=tuple(dict.fromkeys((position_id, *narrowed))),
        evidence={**evidence, "conflict": "message names another contract"},
    )


def _by_contract(
    book: Mapping[uuid.UUID, PositionState], ev: Evidence
) -> tuple[tuple[uuid.UUID, ...], dict[str, Any]]:
    """(candidates, evidence used). Narrowing never empties the set: a word that matches no
    position (over-found ticker, a price) is not evidence."""
    ids = tuple(book)
    used: dict[str, Any] = {}
    named = [p for p in ids if book[p].underlying.upper() in ev.tickers]
    if named:
        ids = tuple(named)
        used["tickers"] = sorted({book[p].underlying for p in named})
        strikes = ev.strikes  # with a ticker, a bare number can be the strike
    else:
        strikes = ev.suffixed_strikes  # without one, only a "765p"-shaped token is
    by_strike = [p for p in ids if book[p].strike is not None and book[p].strike in strikes]
    if by_strike:
        ids = tuple(by_strike)
        used["strikes"] = sorted({str(book[p].strike) for p in by_strike})
    by_right = [p for p in ids if book[p].option_right in ev.rights]
    if by_right:
        ids = tuple(by_right)
        used["rights"] = sorted(ev.rights)
    return ids, used
