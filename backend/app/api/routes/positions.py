import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.ledger import PositionDetail, PositionOut
from app.ledger import queries
from app.ledger.models import PositionStatus

router = APIRouter(tags=["positions"])


@router.get("/positions")
async def list_positions(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    status: PositionStatus | None = None,
    provider_id: Annotated[int | None, Query(alias="providerId")] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[PositionOut]:
    async with sessions() as db:
        items, nxt = await queries.list_positions(
            db, status=status, provider_id=provider_id, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)


@router.get("/positions/{position_id}")
async def get_position(
    position_id: uuid.UUID, sessions: SessionFactoryDep, _ctx: SessionDep
) -> PositionDetail:
    async with sessions() as db:
        position = await queries.get_position(db, position_id)
    if position is None:
        raise ApiError("position not found", "not_found", 404)
    return position
