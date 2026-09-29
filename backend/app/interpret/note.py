"""Advisory note (PRD §4, C80, AC-13): a short plain-language explanation written after the card
is shown, from provider-only context. It can never change a card: only `cards.note` and
`cards.note_hidden` are written. The note guard hides any note that names a ticker, number or
expiry the validated fields and source messages do not contain (OD-6)."""

import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.cards.models import Card
from app.db import notify, transaction
from app.inbox.models import MessageEvent
from app.interpret import context_repo
from app.interpret.candidates import STOP_WORDS, find_candidates
from app.interpret.llm_extract.agent import load_prompt
from app.interpret.llm_extract.middleware import ScopeViolation, scope_violation
from app.interpret.models import Signal
from app.llm.chain import Attempt, AttemptFailed, LlmChain
from app.llm.models import CallOutcome
from app.llm.usage import CallRef
from app.markettime import resolve_expiry_token
from app.runtime_config.schema import RuntimeConfig
from app.validate.normalize import normalize_symbol

logger = logging.getLogger(__name__)

MAX_CHARS = 280
# Card proposal keys that describe the contract as shown. Never qty, lots or breakeven options
# (the user's own numbers, FR-8).
PROPOSAL_KEYS = ("underlying", "expiry", "strike", "right", "limit_price", "signal_price")
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|\.\d+")


class NoteOut(BaseModel):
    note: str = Field(max_length=MAX_CHARS, description="Plain text, at most 280 characters.")


@dataclass
class AllowedValues:
    tickers: set[str] = field(default_factory=set)
    numbers: set[Decimal] = field(default_factory=set)
    expiries: set[date] = field(default_factory=set)


@dataclass(frozen=True)
class GuardResult:
    shown: bool
    offending: tuple[str, ...] = ()


def _numbers(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for raw in _NUMBER.findall(text):
        try:
            out.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:  # pragma: no cover — the regex only matches numbers
            continue
    return out


def _note_ticker(text: str) -> bool:
    """In a note, a ticker is an upper-case word (the model writes them so), not English."""
    # ponytail: casing heuristic; a lower-case ticker in a note is not checked.
    word = text.lstrip("$")
    return (text.startswith("$") or (word.isupper() and len(word) >= 2)) and (
        word.lower() not in STOP_WORDS
    )


def allowed_from(
    values: list[str], texts: list[str], cfg: RuntimeConfig, now: datetime
) -> AllowedValues:
    """Validated field values plus everything code finds in the source messages."""
    allowed = AllowedValues()
    for text in [*values, *texts]:
        found = find_candidates(text)
        for c in found.tickers:
            allowed.tickers.add(normalize_symbol(c.text, cfg.symbol_map, {})[0])
        for c in found.numbers:
            allowed.numbers |= _numbers(c.text)
        for c in found.expiry_tokens:
            if (d := resolve_expiry_token(c.text, now)) is not None:
                allowed.expiries.add(d)
    for v in values:
        allowed.numbers |= _numbers(v)
        try:
            allowed.expiries.add(date.fromisoformat(v))
        except ValueError:
            pass
        allowed.tickers.add(normalize_symbol(v, cfg.symbol_map, {})[0])
    return allowed


def note_guard(note: str, allowed: AllowedValues, cfg: RuntimeConfig, now: datetime) -> GuardResult:
    """Every ticker (after the symbol map), strike/price/level and expiry in the note must be
    an allowed value. Percentages are not prices and are not checked."""
    found = find_candidates(note)
    bad: list[str] = []
    for c in found.tickers:
        if _note_ticker(c.text):
            symbol = normalize_symbol(c.text, cfg.symbol_map, {})[0]
            if symbol not in allowed.tickers:
                bad.append(c.text)
    for c in found.numbers:
        if c.text.endswith("%"):
            continue
        if not _numbers(c.text) <= allowed.numbers:
            bad.append(c.text)
    for c in found.expiry_tokens:
        resolved = resolve_expiry_token(c.text, now)
        if resolved is None or resolved not in allowed.expiries:
            bad.append(c.text)
    return GuardResult(shown=not bad, offending=tuple(bad))


def _field_values(fields: dict[str, Any]) -> list[str]:
    """Plain values of the signal's fields (the provider's own, as validated)."""
    out: list[str] = []
    for v in fields.values():
        items = v if isinstance(v, list) else [v]
        out += [str(i["value"]) for i in items if isinstance(i, dict) and "value" in i]
    return out


class NoteWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        chain: LlmChain,
        alerts: AlertService,
        config: Callable[[], RuntimeConfig],
    ) -> None:
        self._sessions = session_factory
        self._chain = chain
        self._alerts = alerts
        self._config = config

    async def write(self, card_id: uuid.UUID) -> bool:
        """Off the critical path (spawned after notify). True = a note was stored."""
        cfg: RuntimeConfig = self._config()
        loaded = await self._load(card_id)
        if loaded is None:
            return False
        card, signal, texts = loaded
        contract = {k: card.proposal[k] for k in PROPOSAL_KEYS if card.proposal.get(k) is not None}
        values = [*_field_values(signal.fields), *(str(v) for v in contract.values())]
        payload = {
            "action": card.action,
            "card_type": card.card_type,
            "msg_type": signal.msg_type,
            "contract": contract,
            "fields": {k: v for k, v in signal.fields.items() if v not in (None, [], False)},
            "source_messages": texts,
        }
        user = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)

        async def attempt(a: Attempt) -> str:
            a.request = {"prompt": a.entry.prompt, "input": payload}
            if reason := scope_violation(user, frozenset()):
                raise ScopeViolation(reason)
            model = a.model.with_structured_output(NoteOut, include_raw=True)
            prompt = load_prompt(a.entry.prompt)
            out = await model.ainvoke([SystemMessage(prompt), HumanMessage(user)])
            assert isinstance(out, dict)  # noqa: S101 — include_raw=True returns a dict
            raw = out["raw"]
            if isinstance(raw, AIMessage):
                a.usage = raw.usage_metadata
            parsed = out.get("parsed")
            if not isinstance(parsed, NoteOut):
                raise AttemptFailed(CallOutcome.SCHEMA_INVALID, str(out.get("parsing_error")))
            if not parsed.note.strip():
                raise AttemptFailed(CallOutcome.EMPTY, "empty note")
            a.response = {"note": parsed.note}
            return parsed.note.strip()

        result = await self._chain.run("note", attempt, ref=CallRef(card_id=card_id))
        if result.status != "ok" or result.value is None:
            return False  # non-critical: the model_calls rows are the record
        note = result.value
        guard = GuardResult(shown=True)
        if cfg.note_guard_enabled:
            allowed = allowed_from(values, texts, cfg, card.posted_at)
            guard = note_guard(note, allowed, cfg, card.posted_at)
        async with self._sessions() as session, transaction(session):
            await session.execute(
                update(Card)
                .where(col(Card.card_id) == card_id)
                .values(note=note, note_hidden=not guard.shown)  # nothing else, ever (AC-13)
            )
            await notify(session, "cards", json.dumps({"type": "card.updated", "id": str(card_id)}))
            if not guard.shown:
                await self._alerts.raise_(
                    AlertKind.NOTE_HIDDEN,
                    Severity.INFO,
                    "Advisory note hidden: it names values the card does not contain",
                    details={"offending": list(guard.offending)},
                    card_id=card_id,
                    db=session,
                )
        return True

    async def _load(self, card_id: uuid.UUID) -> tuple[Card, Signal, list[str]] | None:
        async with self._sessions() as session:
            card = await session.get(Card, card_id)
            if card is None or card.signal_id is None:
                return None
            signal = await session.get(Signal, card.signal_id)
            if signal is None:
                return None
            event = await session.scalar(
                select(MessageEvent).where(
                    col(MessageEvent.message_id) == signal.message_id,
                    col(MessageEvent.event_type) == signal.event_type,
                    col(MessageEvent.event_seq) == signal.event_seq,
                )
            )
            texts = [event.text] if event is not None and event.text else []
            for source_id in _context_sources(signal.fields):
                found = await context_repo.message_as_of(session, source_id, card.posted_at)
                if found is not None:
                    texts.append(found[1])
        return card, signal, texts


def _context_sources(fields: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for v in fields.values():
        for item in v if isinstance(v, list) else [v]:
            source = item.get("source") if isinstance(item, dict) else None
            if isinstance(source, dict) and source.get("kind") == "context":
                ids.append(int(source["source_message_id"]))
    return sorted(set(ids))
