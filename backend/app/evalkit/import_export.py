"""Historical export -> `message_events` (C65, F048). PROVISIONAL, like the ingest adapter it
reuses: the export is a JSON list of Message API payloads (OD-1); F050 replaces the format.

Events are stored `backfilled=true` and never queued: they are labeled and replayed, never
processed live. Re-importing the same file adds nothing."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import transaction
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
