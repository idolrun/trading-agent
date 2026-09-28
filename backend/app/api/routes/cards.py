import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.cards import CardDetail, CardOut
from app.cards import queries
from app.cards.models import CardState

router = APIRouter(tags=["cards"])


@router.get("/cards")
async def list_cards(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    state: CardState | None = None,
    provider_id: Annotated[int | None, Query(alias="providerId")] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[CardOut]:
    async with sessions() as db:
        items, nxt = await queries.list_cards(
            db, state=state, provider_id=provider_id, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)


@router.get("/cards/{card_id}")
async def get_card(card_id: uuid.UUID, sessions: SessionFactoryDep, _ctx: SessionDep) -> CardDetail:
    async with sessions() as db:
        card = await queries.get_card(db, card_id)
    if card is None:
        raise ApiError("card not found", "not_found", 404)
    return card
