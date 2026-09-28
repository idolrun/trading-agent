import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.orders import FillOut, OrderDetail, OrderOut
from app.execution import queries
from app.execution.models import OrderStatus

router = APIRouter(tags=["orders"])


@router.get("/orders")
async def list_orders(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    status: OrderStatus | None = None,
    lot_id: Annotated[uuid.UUID | None, Query(alias="lotId")] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[OrderOut]:
    async with sessions() as db:
        items, nxt = await queries.list_orders(
            db, status=status, lot_id=lot_id, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)


@router.get("/orders/{order_id}")
async def get_order(
    order_id: uuid.UUID, sessions: SessionFactoryDep, _ctx: SessionDep
) -> OrderDetail:
    async with sessions() as db:
        order = await queries.get_order(db, order_id)
    if order is None:
        raise ApiError("order not found", "not_found", 404)
    return order


@router.get("/fills")
async def list_fills(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    order_id: Annotated[uuid.UUID | None, Query(alias="orderId")] = None,
    since: datetime | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[FillOut]:
    async with sessions() as db:
        items, nxt = await queries.list_fills(
            db, order_id=order_id, since=since, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)
