"""Lane-scoped context for one message (C71, F068), rebuilt from Postgres on every run.

The lane is (channel, provider): earlier messages and the book come from this author in this
channel only. Another author's text appears only as the reply parent, read-only (never a
source a model may copy a value from). Structurally cannot carry the user's lots, balances or
P&L (FR-8): `extra="forbid"` models with no field for them, and the only book is the lane's own
(AC-18).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.envelope import Envelope
from app.inbox.models import MessageEvent
from app.interpret import context_repo
from app.ledger.models import Instrument, PositionStatus, ProviderPosition, ProviderPositionEvent
from app.markettime import to_et

# Checked against the JSON schema by a unit test and by the LLM scope guard (F032).
FORBIDDEN_KEYS = frozenset({"lot", "lots", "balance", "pnl", "equity", "avg_price", "qty_held"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextMessage(_Strict):
    id: str  # Discord IDs as strings (exact in JSON)
    author_id: str
    at_et: str
    text: str


class CurrentMessage(_Strict):
    id: str
    author_id: str
    at_et: str
    text: str | None  # None for a delete event
    event_type: str
    channel_id: str
    reply_to: str | None
    thread_id: str | None = None  # F067: posted in this thread of the channel


class BookEntry(_Strict):
    id: uuid.UUID
    summary: str


class SignalEvent(_Strict):
    """F073: one thing the provider recently did to one of the lane's signals."""

    position_id: uuid.UUID
    at_et: str
    summary: str  # "trim SPY 765P" / "watch SPY" / "open QQQ 745P"


class ProviderContext(_Strict):
    now_et: str
    message: CurrentMessage
    reply_parent: ContextMessage | None  # any author; read-only context (never linked across)
    recent_author_messages: list[ContextMessage]  # same lane, posted before, oldest first
    provider_book: list[BookEntry]  # this lane's open/trimmed positions only
    # the positions the message may act on, narrowed by the context resolver (F069); the only
    # `links_to` options a model is offered. Not given: the whole book (nothing narrowed yet).
    link_candidates: list[BookEntry] = []
    # F073: the lane's latest position events (newest first), e.g. which signal was trimmed last
    recent_signal_events: list[SignalEvent] = []

    @model_validator(mode="before")
    @classmethod
    def _candidates_default_to_book(cls, data: Any) -> Any:
        if isinstance(data, dict) and "link_candidates" not in data:
            return {**data, "link_candidates": data.get("provider_book", [])}
        return data

    provider_profile: dict[str, list[str]] = {}  # lexicon/aliases from the store (F035)


async def load_context(
    db: AsyncSession,
    env: Envelope,
    *,
    recent_n: int,
    now: datetime,
    profile: dict[str, list[str]] | None = None,
    book: list[BookEntry] | None = None,
) -> ProviderContext:
    """`recent_n` = config.context_recent_messages, the same window everywhere (F073). `book`
    replaces the provider's stored book (evalkit replay: the label ledger, F048)."""
    cutoff = env.posted_at  # post time, never arrival order: replay-deterministic
    parent = None
    if env.reply_to_id is not None:
        found = await context_repo.message_as_of(db, env.reply_to_id, cutoff)
        parent = _message(*found) if found else None
    recent = await context_repo.recent_by_author(db, env.lane, cutoff, env.key.message_id, recent_n)
    if book is None:
        book = [
            BookEntry(id=p.provider_position_id, summary=summarize(p))
            for p in await context_repo.open_book(db, env.lane, env.posted_at)
        ]
    events = [
        SignalEvent(position_id=p.provider_position_id, at_et=_et(at), summary=_event(e, p))
        for e, p, at in await context_repo.recent_signal_events(db, env.lane, cutoff, recent_n)
    ]
    return ProviderContext(
        now_et=_et(now),
        message=CurrentMessage(
            id=str(env.key.message_id),
            author_id=str(env.provider_id),
            at_et=_et(env.posted_at),
            text=env.text,
            event_type=env.key.event_type,
            channel_id=str(env.channel_id),
            reply_to=None if env.reply_to_id is None else str(env.reply_to_id),
            thread_id=None if env.thread_id is None else str(env.thread_id),
        ),
        reply_parent=parent,
        recent_author_messages=[_message(*row) for row in recent],
        provider_book=book,
        recent_signal_events=events,
        provider_profile=profile or {},
    )


def summarize(p: ProviderPosition) -> str:
    """Deterministic, byte-stable one-line summary (prompt caching, TRD §4.8 rule 7)."""
    if p.status in (PositionStatus.WATCH, PositionStatus.CONDITIONAL):  # F072: not a holding
        named = [p.underlying]
        if p.strike is not None:
            named.append(_plain(p.strike))
        if p.option_right is not None:
            named.append(f"{p.option_right}s")
        line = f"{p.status} {' '.join(named)} since {_et(p.opened_at)}"
        return f"{line}: {p.condition_text}" if p.condition_text else line
    if p.instrument == Instrument.OPTION:
        right = "C" if p.option_right == "call" else "P"
        contract = f"{p.underlying} {_plain(p.strike)}{right} exp {p.expiry}"
    else:
        contract = f"{p.underlying} stock"
    parts = [contract]
    if p.provider_entry_price is not None:
        parts.append(f"entered {_plain(p.provider_entry_price)} at {_et(p.opened_at)}")
    else:
        parts.append(f"opened at {_et(p.opened_at)}")
    if p.status == PositionStatus.TRIMMED:
        parts.append(f"trimmed, {_plain(p.remaining_fraction * 100)}% remaining")
    if p.stop_level is not None:
        parts.append(f"stop {_plain(p.stop_level)} ({p.stop_basis or 'premium'})")
    if p.tp_levels:
        parts.append("TPs " + "/".join(_plain(t) for t in p.tp_levels))
    return ", ".join(parts)


def _event(e: ProviderPositionEvent, p: ProviderPosition) -> str:
    named = [p.underlying]
    if p.strike is not None:
        right = "" if p.option_right is None else ("C" if p.option_right == "call" else "P")
        named.append(f"{_plain(p.strike)}{right}")
    return f"{e.change} {' '.join(named)}"


def _message(row: MessageEvent, text: str) -> ContextMessage:
    return ContextMessage(
        id=str(row.message_id), author_id=str(row.provider_id), at_et=_et(row.posted_at), text=text
    )


def _et(ts: datetime) -> str:
    return to_et(ts).strftime("%Y-%m-%d %H:%M:%S ET")


def _plain(value: Decimal | None) -> str:
    return "?" if value is None else format(value.normalize(), "f")
