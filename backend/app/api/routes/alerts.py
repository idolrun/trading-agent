import uuid

from fastapi import APIRouter

from app.alerts import queries
from app.alerts.models import Severity
from app.alerts.service import AlertAlreadyAcknowledged, AlertNotFound
from app.api.deps import AlertsDep, SessionDep, SessionFactoryDep
from app.api.errors import ApiError
from app.api.pagination import Cursor, Limit, Page
from app.api.schemas.alerts import AlertOut

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def list_alerts(
    sessions: SessionFactoryDep,
    _ctx: SessionDep,
    acknowledged: bool | None = None,
    severity: Severity | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[AlertOut]:
    async with sessions() as db:
        items, nxt = await queries.list_alerts(
            db, acknowledged=acknowledged, severity=severity, cursor=cursor, limit=limit
        )
    return Page(items=items, next_cursor=nxt)


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(
    alert_id: uuid.UUID, alerts: AlertsDep, sessions: SessionFactoryDep, ctx: SessionDep
) -> AlertOut:
    try:
        await alerts.acknowledge(alert_id, ctx.session_id)
    except AlertNotFound:
        raise ApiError("alert not found", "not_found", 404) from None
    except AlertAlreadyAcknowledged:
        raise ApiError("alert already acknowledged", "conflict", 409) from None
    async with sessions() as db:
        alert = await queries.get_alert(db, alert_id)
    assert alert is not None  # noqa: S101 — just acknowledged
    return alert
