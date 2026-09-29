import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError

from app.api.deps import AuditDep, AuthDep, ClientDep, SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.card_decisions import DecisionRequest
from app.api.schemas.cards import CardDetail, CardOut
from app.auth.models import AuditAction
from app.cards import queries
from app.cards.approval_service import (
    ApprovalService,
    CardNotFound,
    DecisionConflict,
    DecisionInvalid,
    StepUpRequired,
)
from app.cards.edits import Edits
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


@router.post("/cards/{card_id}/decision")
async def decide_card(
    card_id: uuid.UUID,
    body: DecisionRequest,
    request: Request,
    sessions: SessionFactoryDep,
    auth: AuthDep,
    audit: AuditDep,
    client: ClientDep,
    ctx: SessionDep,
) -> CardDetail:
    approvals: ApprovalService = request.app.state.approvals
    try:
        edits = Edits.model_validate(
            body.edits.model_dump(by_alias=False, exclude_none=True) if body.edits else {}
        )
        await approvals.decide(
            card_id,
            body.decision,
            body.version,
            edits,
            session_id=ctx.session_id,
            consume_step_up=lambda: auth.consume_step_up(ctx),
        )
    except CardNotFound:
        raise ApiError("card not found", "not_found", 404) from None
    except DecisionConflict as exc:
        raise ApiError(str(exc), "conflict", 409) from None
    except (DecisionInvalid, ValidationError) as exc:
        raise ApiError(str(exc), "invalid", 422) from None
    except StepUpRequired:
        raise ApiError("confirm your password first", "step_up_required", 403) from None
    action = {
        "approve": AuditAction.TRADE_APPROVED,
        "reject": AuditAction.TRADE_REJECTED,
        "edit": AuditAction.CARD_EDITED,
    }[body.decision]
    await audit.by(ctx, client, action, "card", card_id, version=body.version)
    return await get_card(card_id, sessions, ctx)
