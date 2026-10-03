"""Labeling (F048, PRD §17, C64): the queue of unlabeled messages with the LLM's suggestion, and
label writes. Labels are append-only: a write supersedes the current label (F061 screen)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ConfigServiceDep, SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Page
from app.api.schemas.labels import LabelDetail, LabelProgress, LabelPut, QueueItem
from app.db import transaction
from app.evalkit import labels
from app.evalkit.gate import MIN_ACTIONABLE, MIN_NON_ACTIONABLE, MIN_PROVIDERS
from app.evalkit.metrics import is_actionable
from app.evalkit.models import LabelSource
from app.inbox.envelope import EventKey
from app.inbox.models import MessageEvent
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService

router = APIRouter(tags=["labels"])


def _key(raw: str) -> EventKey:
    try:
        return labels.parse_event_key(raw)
    except ValueError:
        raise ApiError("unknown message event", "not_found", 404) from None


def _recent_n(config: ConfigService) -> int:
    cfg = config.get()
    return (cfg or RuntimeConfig()).context_recent_messages  # F073: the live window


@router.get("/labels/queue")
async def queue(
    sessions: SessionFactoryDep,
    config: ConfigServiceDep,
    _ctx: SessionDep,
    provider_id: Annotated[int | None, Query(alias="providerId")] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[QueueItem]:
    async with sessions() as db:
        events, nxt = await labels.unlabeled(
            db, provider_id=provider_id, cursor=cursor, limit=limit
        )
        items = [await labels.queue_item(db, e, _recent_n(config)) for e in events]
    return Page(items=items, next_cursor=nxt)


@router.get("/labels/progress")
async def progress(sessions: SessionFactoryDep, _ctx: SessionDep) -> LabelProgress:
    async with sessions() as db:
        labeled = await labels.latest_labels(db)
    actionable = sum(
        any(is_actionable(sig["msg_type"]) for sig in item.label.get("signals", []))
        for item in labeled
    )
    return LabelProgress(
        actionable=actionable,
        non_actionable=len(labeled) - actionable,
        providers=len({item.envelope.provider_id for item in labeled}),
        min_actionable=MIN_ACTIONABLE,
        min_non_actionable=MIN_NON_ACTIONABLE,
        min_providers=MIN_PROVIDERS,
    )


async def _detail(sessions: SessionFactoryDep, config: ConfigService, key: EventKey) -> LabelDetail:
    async with sessions() as db:
        event = await db.get(MessageEvent, tuple(key))
        if event is None or event.text is None:
            raise ApiError("unknown message event", "not_found", 404)
        item = await labels.queue_item(db, event, _recent_n(config))
        chain = await labels.history(db, key)
    return LabelDetail(**dict(item), labels=[labels.record(r) for r in chain])


@router.get("/labels/{event_key}")
async def get_label(
    event_key: str, sessions: SessionFactoryDep, config: ConfigServiceDep, _ctx: SessionDep
) -> LabelDetail:
    return await _detail(sessions, config, _key(event_key))


@router.put("/labels/{event_key}")
async def put_label(
    event_key: str,
    body: LabelPut,
    sessions: SessionFactoryDep,
    config: ConfigServiceDep,
    ctx: SessionDep,
) -> LabelDetail:
    key = _key(event_key)
    async with sessions() as db, transaction(db):
        suggested = await labels.pending_prelabel(db, key) is not None
        try:
            await labels.add_label(
                db,
                key,
                labels.stored(body.label),
                source=LabelSource.PRELABEL_CORRECTED if suggested else LabelSource.MANUAL,
                session_id=ctx.session_id,
                supersedes=body.supersedes,
            )
        except LookupError:
            raise ApiError("unknown message event", "not_found", 404) from None
        except labels.LabelConflict as exc:
            raise ApiError(str(exc), "conflict", 409) from None
    return await _detail(sessions, config, key)
