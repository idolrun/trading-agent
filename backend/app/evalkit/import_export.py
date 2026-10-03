"""Historical export -> `message_events` (C65, F048), and a label file -> `eval_labels` (F082).

The export is PROVISIONAL, like the ingest adapter it reuses: a JSON list of Message API payloads
(OD-1); F050 replaces the format. Events are stored `backfilled=true` and never queued: they are
labeled and replayed, never processed live. Re-importing the same file adds nothing.

A label file is `{"source": "assistant", "labels": {"<event key>": <Label>}}`. A link may name
the position by the message that opened it: `"provider_position_id": "open:<event key>[:idx]"`
(the replay ledger's id for it, `replay.position_id_of`)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.labels import Label
from app.db import transaction
from app.evalkit.labels import add_label, history, parse_event_key, stored
from app.evalkit.models import LabelSource
from app.inbox import repo as inbox
from app.ingest.adapter import UnmappablePayload, to_envelope

logger = logging.getLogger(__name__)
CHUNK = 500


@dataclass(frozen=True)
class ImportResult:
    read: int
    inserted: int
    duplicates: int
    unmappable: int


async def import_history(
    sessions: async_sessionmaker[AsyncSession], payloads: list[Any], now: datetime
) -> ImportResult:
    inserted = unmappable = 0
    for start in range(0, len(payloads), CHUNK):
        async with sessions() as db, transaction(db):
            for payload in payloads[start : start + CHUNK]:
                try:
                    env = to_envelope(payload, now).model_copy(update={"backfilled": True})
                except UnmappablePayload as exc:
                    logger.warning("unmappable history payload", extra={"error": str(exc)[:200]})
                    unmappable += 1
                    continue
                inserted += await inbox.store_event(db, env)
    return ImportResult(len(payloads), inserted, len(payloads) - inserted - unmappable, unmappable)


@dataclass(frozen=True)
class LabelImport:
    read: int
    inserted: int
    unchanged: int  # the event's current label is already this one
    invalid: list[str]  # "<event key>: why"


def _expand(label: dict[str, Any]) -> dict[str, Any]:
    from app.evalkit.replay import position_id_of  # replay imports the graph: keep it lazy

    link: dict[str, Any] = label.get("link") or {}
    for name in ("provider_position_id", "providerPositionId"):  # snake_case or the API's
        target = link.get(name)
        if isinstance(target, str) and target.startswith("open:"):
            key, _, idx = target.removeprefix("open:").partition(":")
            link = {**link, name: str(position_id_of(key, int(idx or 0)))}
            return {**label, "link": link}
    return label


async def import_labels(
    sessions: async_sessionmaker[AsyncSession], doc: dict[str, Any]
) -> LabelImport:
    source = LabelSource(doc.get("source", LabelSource.ASSISTANT))
    labels: dict[str, Any] = doc["labels"]
    inserted = unchanged = 0
    invalid: list[str] = []
    async with sessions() as db, transaction(db):
        for raw_key, raw in labels.items():
            try:
                key = parse_event_key(raw_key)
                label = stored(Label.model_validate(_expand(raw)))
            except (ValueError, ValidationError) as exc:
                invalid.append(f"{raw_key}: {str(exc)[:200]}")
                continue
            chain = await history(db, key)
            current = next(
                (r for r in chain if not any(o.supersedes_label_id == r.label_id for o in chain)),
                None,
            )
            if current is not None and current.label == label:
                unchanged += 1
                continue
            try:
                await add_label(db, key, label, source=source, session_id=None)
            except LookupError as exc:
                invalid.append(f"{raw_key}: {exc}")
                continue
            inserted += 1
    return LabelImport(len(labels), inserted, unchanged, invalid)
