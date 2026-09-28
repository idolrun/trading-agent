import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.ledger import LotDetail, LotOut
from app.ledger import queries
from app.ledger.models import LotStatus

router = APIRouter(tags=["lots"])


@router.get("/lots")
async def list_lots(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    status: LotStatus | None = None,
    foreign: bool | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[LotOut]:
    async with sessions() as db:
        items, nxt = await queries.list_lots(
            db, status=status, foreign=foreign, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)


@router.get("/lots/{lot_id}")
async def get_lot(lot_id: uuid.UUID, sessions: SessionFactoryDep, _ctx: SessionDep) -> LotDetail:
    async with sessions() as db:
        lot = await queries.get_lot(db, lot_id)
    if lot is None:
        raise ApiError("lot not found", "not_found", 404)
    return lot
