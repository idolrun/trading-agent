"""Message API webhook (TRD §2.4): 200 only after the event is committed; 503 if it cannot be."""

import hashlib
import json
import logging

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from app.alerts.models import AlertKind, Severity
from app.api.deps import AlertsDep, ClockDep, SessionFactoryDep, SettingsDep
from app.api.errors import ApiError
from app.api.schemas.base import CamelModel
from app.db import in_transaction
from app.inbox import repo as inbox
from app.ingest.adapter import UnmappablePayload, to_envelope
from app.ingest.security import verify

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


class IngestResponse(CamelModel):
    accepted: bool  # false = duplicate delivery (already stored)


async def signed_body(request: Request, settings: SettingsDep, clock: ClockDep) -> bytes:
    """HMAC over the raw bytes, checked before anything is parsed."""
    body = await request.body()
    if not verify(
        settings.ingest_hmac_secret.get_secret_value().encode(),
        request.headers.get("x-timestamp"),
        request.headers.get("x-signature"),
        body,
        clock.now(),
        settings.ingest_max_skew_s,
    ):
        logger.warning("ingest signature rejected")
        raise ApiError("invalid signature or timestamp", "unauthorized", 401)
    return body


@router.post("/ingest")
async def ingest(
    request: Request,
    settings: SettingsDep,
    clock: ClockDep,
    factory: SessionFactoryDep,
    alerts: AlertsDep,
) -> IngestResponse:
    body = await signed_body(request, settings, clock)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise UnmappablePayload("payload is not a JSON object")
        env = to_envelope(payload, clock.now())
    except (ValueError, UnmappablePayload) as exc:
        await _contract_alert(alerts, body, str(exc))
        raise ApiError("payload does not match the Message API contract", "invalid", 422) from None
    try:
        async with in_transaction(factory, None) as db:
            accepted = await inbox.accept(db, env)
    except (SQLAlchemyError, OSError):
        logger.exception("ingest storage failed", extra={"message_id": env.key.message_id})
        raise ApiError("storage unavailable; redeliver later", "unavailable", 503) from None
    feed_health = getattr(request.app.state, "feed_health", None)
    if feed_health is not None:
        feed_health.note_message(clock.now())
    return IngestResponse(accepted=accepted)


async def _contract_alert(alerts: AlertsDep, body: bytes, reason: str) -> None:
    """Best effort (the DB may be what is failing); one alert per distinct payload."""
    try:
        await alerts.raise_(
            AlertKind.MESSAGE_API,
            Severity.URGENT,
            "Message API payload could not be mapped (contract changed?)",
            details={"reason": reason[:500]},
            dedupe_key=f"unmappable:{hashlib.sha256(body).hexdigest()}",
        )
    except Exception:
        logger.exception("could not record unmappable-payload alert")
