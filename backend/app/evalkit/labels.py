"""Labels (PRD §17, C64, C67): event keys, the pending LLM prelabel, the append-only label
chain, and the prelabel batch.

`eval_labels` is insert-only: a correction inserts a new row that names the one it replaces
(`supersedes_label_id`); the current label of an event is the row nothing supersedes. The LLM
prelabel is not a table: it is the latest ok `prelabel` row of `model_calls` for the event.
Nothing here sends the user's own trades to a model (C87): the prompt input is the message
and the provider's own earlier messages.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased
from sqlmodel import col

from app.api.pagination import cut, keyset
from app.api.schemas.labels import ContextMessageOut, Label, LabelRecord, QueueItem
from app.evalkit.models import EvalLabel, LabelSource, Split
from app.evalkit.split import split_for
from app.inbox import repo as inbox
from app.inbox.envelope import Envelope, EventKey
from app.inbox.models import EventType, MessageEvent
from app.interpret.context import load_context
from app.interpret.llm_extract.agent import load_prompt
from app.interpret.llm_extract.middleware import scope_violation
from app.llm.chain import Attempt, AttemptFailed, LlmChain
from app.llm.models import CallOutcome, ModelCall, ModelRole
from app.llm.usage import CallRef
from app.markettime import to_et

logger = logging.getLogger(__name__)


def event_key(key: EventKey) -> str:
    """`<message_id>.<event_type>.<event_seq>`"""
    return f"{key.message_id}.{key.event_type}.{key.event_seq}"


def parse_event_key(raw: str) -> EventKey:
    try:
        message_id, event_type, event_seq = raw.split(".")
        return EventKey(int(message_id), EventType(event_type), int(event_seq))
    except ValueError:
        raise ValueError(f"invalid event key: {raw!r}") from None


def stored(label: Label) -> dict[str, Any]:
    """The `eval_labels.label` form: snake_case, JSON values, unset keys left out."""
    return label.model_dump(mode="json", by_alias=False, exclude_none=True)


def _same_key(model: Any, key: EventKey) -> list[Any]:
    return [
        col(model.message_id) == key.message_id,
        col(model.event_type) == key.event_type,
        col(model.event_seq) == key.event_seq,
    ]


@dataclass(frozen=True)
class Labeled:
    """A message event with its current label."""

    envelope: Envelope
    label: dict[str, Any]
    split: Split
    label_id: uuid.UUID
    day: date


async def latest_labels(db: AsyncSession) -> list[Labeled]:
    """The current label of every labeled event, in post-time order (the dispatcher's order)."""
    newer = aliased(EvalLabel)
    rows = await db.execute(
        select(EvalLabel, MessageEvent)
        .join(
            MessageEvent,
            (col(MessageEvent.message_id) == col(EvalLabel.message_id))
            & (col(MessageEvent.event_type) == col(EvalLabel.event_type))
            & (col(MessageEvent.event_seq) == col(EvalLabel.event_seq)),
        )
        .where(~exists().where(col(newer.supersedes_label_id) == col(EvalLabel.label_id)))
        .order_by(
            col(MessageEvent.posted_at),
            col(MessageEvent.message_id),
            col(MessageEvent.event_type),
            col(MessageEvent.event_seq),
            col(EvalLabel.labeled_at),
        )
    )
    current: dict[EventKey, Labeled] = {}  # a forked chain: the newest fork wins
    for label, event in rows.tuples():
        env = inbox.to_envelope_row(event)
        current[env.key] = Labeled(env, label.label, label.split, label.label_id, label.trading_day)
    return list(current.values())


async def history(db: AsyncSession, key: EventKey) -> list[EvalLabel]:
    """Every label of the event, newest first."""
    rows = await db.scalars(
        select(EvalLabel)
        .where(*_same_key(EvalLabel, key))
        .order_by(col(EvalLabel.labeled_at).desc())
    )
    return list(rows.all())


def record(row: EvalLabel) -> LabelRecord:
    return LabelRecord(
        label_id=row.label_id,
        label=Label.model_validate(row.label),
        source=row.source,
        split=row.split,
        trading_day=row.trading_day,
        labeled_at=row.labeled_at,
        supersedes_label_id=row.supersedes_label_id,
    )


async def pending_prelabel(db: AsyncSession, key: EventKey) -> Label | None:
    """The LLM's suggestion: the latest ok `prelabel` answer for the event."""
    response = await db.scalar(
        select(col(ModelCall.response))
        .where(
            *_same_key(ModelCall, key),
            col(ModelCall.role) == ModelRole.PRELABEL,
            col(ModelCall.outcome) == CallOutcome.OK,
        )
        .order_by(col(ModelCall.id).desc())
        .limit(1)
    )
    try:
        return None if response is None else Label.model_validate(response)
    except ValidationError:
        return None


class LabelConflict(Exception):
    """`supersedes` is not the event's current label (someone labeled it since)."""


async def add_label(
    db: AsyncSession,
    key: EventKey,
    label: dict[str, Any],
    *,
    source: LabelSource,
    session_id: uuid.UUID | None,
    supersedes: uuid.UUID | None = None,
) -> EvalLabel:
    """Insert the event's new current label; the split follows its trading day (C67).
    `supersedes` (default: the current label) must be the current one."""
    event = await db.get(MessageEvent, tuple(key))
    if event is None:
        raise LookupError(f"unknown message event {event_key(key)}")
    chain = await history(db, key)
    current = next(
        (r.label_id for r in chain if not any(o.supersedes_label_id == r.label_id for o in chain)),
        None,
    )
    if supersedes is not None and supersedes != current:
        raise LabelConflict(f"{supersedes} is not the current label of {event_key(key)}")
    day = to_et(event.posted_at).date()
    row = EvalLabel(
        message_id=key.message_id,
        event_type=key.event_type,
        event_seq=key.event_seq,
        label=label,
        trading_day=day,
        split=split_for(day),
        source=source,
        supersedes_label_id=current,
        session_id=session_id,
    )
    db.add(row)
    await db.flush()
    return row


# --- the labeling queue (API) ------------------------------------------------------------


async def label_context(db: AsyncSession, env: Envelope, recent_n: int) -> list[ContextMessageOut]:
    """What the labeler (and the prelabel model) may read: the reply parent and the provider's
    own earlier messages. No book: the ledger of that day is not stored."""
    ctx = await load_context(db, env, recent_n=recent_n, now=env.posted_at, book=[])
    parent = [(ctx.reply_parent, True)] if ctx.reply_parent else []
    messages = [*parent, *((m, False) for m in ctx.recent_author_messages)]
    return [
        ContextMessageOut(id=int(m.id), at_et=m.at_et, text=m.text, is_reply_parent=is_parent)
        for m, is_parent in messages
    ]


async def unlabeled(
    db: AsyncSession, *, provider_id: int | None, cursor: str | None, limit: int
) -> tuple[list[MessageEvent], str | None]:
    """Events with text and no label yet, newest first (keyset page)."""
    labeled = exists().where(*_of_event(EvalLabel))
    query = select(MessageEvent).where(col(MessageEvent.text).is_not(None), ~labeled)
    if provider_id is not None:
        query = query.where(col(MessageEvent.provider_id) == provider_id)
    at, key = col(MessageEvent.posted_at), col(MessageEvent.message_id)
    rows = list((await db.scalars(keyset(query, at, key, cursor, limit, int))).all())
    return cut(rows, limit, lambda r: (r.posted_at, r.message_id))


def _of_event(model: Any) -> list[Any]:
    """Correlates `model` (EvalLabel / ModelCall) with the outer `MessageEvent` row."""
    return [
        col(model.message_id) == col(MessageEvent.message_id),
        col(model.event_type) == col(MessageEvent.event_type),
        col(model.event_seq) == col(MessageEvent.event_seq),
    ]


async def queue_item(db: AsyncSession, event: MessageEvent, recent_n: int) -> QueueItem:
    env = inbox.to_envelope_row(event)
    return QueueItem(
        event_key=event_key(env.key),
        provider_id=event.provider_id,
        posted_at=event.posted_at,
        text=event.text or "",
        context=await label_context(db, env, recent_n),
        prelabel=await pending_prelabel(db, env.key),
    )


# --- prelabel (batch, no deadline) -------------------------------------------------------


async def prelabel_batch(
    sessions: async_sessionmaker[AsyncSession], chain: LlmChain, *, limit: int, recent_n: int
) -> int:
    """Ask the `prelabel` chain for a suggestion on up to `limit` unlabeled events that have
    none yet, oldest first. Returns how many were answered."""
    answered = exists().where(
        *_of_event(ModelCall),
        col(ModelCall.role) == ModelRole.PRELABEL,
        col(ModelCall.outcome) == CallOutcome.OK,
    )
    async with sessions() as db:
        events = (
            await db.scalars(
                select(MessageEvent)
                .where(
                    col(MessageEvent.text).is_not(None),
                    ~exists().where(*_of_event(EvalLabel)),
                    ~answered,
                )
                .order_by(col(MessageEvent.posted_at), col(MessageEvent.message_id))
                .limit(limit)
            )
        ).all()
    done = 0
    for event in events:
        env = inbox.to_envelope_row(event)
        async with sessions() as db:
            ctx = await load_context(db, env, recent_n=recent_n, now=env.posted_at, book=[])
        user = ctx.model_dump_json()  # provider-scoped by construction (C71, C87)

        async def attempt(a: Attempt, user: str = user) -> None:
            a.request = {"prompt": a.entry.prompt, "input": json.loads(user)}
            if reason := scope_violation(user, frozenset()):
                raise AttemptFailed(CallOutcome.SCOPE_BLOCKED, reason)
            model = a.model.with_structured_output(Label, include_raw=True)
            out = await model.ainvoke(
                [SystemMessage(load_prompt(a.entry.prompt)), HumanMessage(user)]
            )
            assert isinstance(out, dict)  # noqa: S101 — include_raw=True returns a dict
            if isinstance(out["raw"], AIMessage):
                a.usage = out["raw"].usage_metadata
            parsed = out.get("parsed")
            if not isinstance(parsed, Label):
                raise AttemptFailed(CallOutcome.SCHEMA_INVALID, str(out.get("parsing_error")))
            a.response = stored(parsed)

        result = await chain.run(
            "prelabel",
            attempt,
            ref=CallRef(
                message_id=env.key.message_id,
                event_type=env.key.event_type,
                event_seq=env.key.event_seq,
            ),
        )
        done += result.status == "ok"
    logger.info("prelabel batch", extra={"events": len(events), "answered": done})
    return done
