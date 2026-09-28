import uuid

from fastapi import APIRouter, Response
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col

from app.api.deps import SessionDep, SessionFactoryDep, SettingsDep
from app.api.errors import ApiError
from app.api.schemas.push import SubscribeRequest, SubscribeResponse, VapidKeyResponse
from app.db import in_transaction
from app.notify.models import PushSubscription

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key")
async def vapid_public_key(settings: SettingsDep, _ctx: SessionDep) -> VapidKeyResponse:
    if not settings.vapid_public_key:
        raise ApiError("web push is not configured", "unavailable", 503)
    return VapidKeyResponse(public_key=settings.vapid_public_key)


@router.post("/subscriptions", status_code=201)
async def subscribe(
    body: SubscribeRequest, sessions: SessionFactoryDep, _ctx: SessionDep
) -> SubscribeResponse:
    """Idempotent on the endpoint: re-subscribing refreshes the keys and un-revokes it."""
    values = {"p256dh": body.keys.p256dh, "auth": body.keys.auth, "label": body.label}
    async with in_transaction(sessions, None) as db:
        subscription_id = await db.scalar(
            pg_insert(PushSubscription)
            .values(subscription_id=uuid.uuid4(), endpoint=body.endpoint, **values)
            .on_conflict_do_update(
                index_elements=[col(PushSubscription.endpoint)],
                set_={**values, "revoked_at": None},
            )
            .returning(col(PushSubscription.subscription_id))
        )
    assert subscription_id is not None  # noqa: S101 — upsert always returns the row
    return SubscribeResponse(subscription_id=subscription_id)


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def unsubscribe(
    subscription_id: uuid.UUID, sessions: SessionFactoryDep, _ctx: SessionDep
) -> Response:
    """Revokes (the runtime role cannot delete; the row stays as history)."""
    async with in_transaction(sessions, None) as db:
        revoked = await db.scalar(
            update(PushSubscription)
            .where(
                col(PushSubscription.subscription_id) == subscription_id,
                col(PushSubscription.revoked_at).is_(None),
            )
            .values(revoked_at=func.now())
            .returning(col(PushSubscription.subscription_id))
        )
    if revoked is None:
        raise ApiError("subscription not found", "not_found", 404)
    return Response(status_code=204)
