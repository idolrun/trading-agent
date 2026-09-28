"""Read side of alerts (FR-26). No writes."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.alerts.models import Alert, AlertAck
from app.api.pagination import cut, keyset
from app.api.schemas.alerts import AlertOut

_acked = exists().where(col(AlertAck.alert_id) == col(Alert.alert_id))


async def list_alerts(
    db: AsyncSession,
    *,
    acknowledged: bool | None,
    severity: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AlertOut], str | None]:
    query = select(Alert, _acked.label("acked"))
    if acknowledged is not None:
        query = query.where(_acked if acknowledged else ~_acked)
    if severity is not None:
        query = query.where(col(Alert.severity) == severity)
    rows = (
        await db.execute(
            keyset(query, col(Alert.created_at), col(Alert.alert_id), cursor, limit, uuid.UUID)
        )
    ).all()
    page, nxt = cut(rows, limit, lambda r: (r[0].created_at, r[0].alert_id))
    return [_out(alert, acked) for alert, acked in page], nxt


async def get_alert(db: AsyncSession, alert_id: uuid.UUID) -> AlertOut | None:
    row = (await db.execute(select(Alert, _acked).where(col(Alert.alert_id) == alert_id))).first()
    return _out(*row) if row else None


def _out(alert: Alert, acked: bool) -> AlertOut:
    return AlertOut(
        id=alert.alert_id,
        severity=alert.severity,
        kind=alert.kind,
        message=alert.message,
        acknowledged=acked,
        card_id=alert.card_id,
        created_at=alert.created_at,
    )
