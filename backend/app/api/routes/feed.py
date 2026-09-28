from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SessionFactoryDep
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.feed import FeedItem
from app.inbox import queries

router = APIRouter(tags=["feed"])
Label = Literal["stale", "late", "backfilled", "muted", "channel_not_configured", "failed"]


@router.get("/feed")
async def feed(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    provider_id: Annotated[int | None, Query(alias="providerId")] = None,
    label: Label | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[FeedItem]:
    async with sessions() as db:
        items, nxt = await queries.list_feed(
            db, provider_id=provider_id, label=label, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)
