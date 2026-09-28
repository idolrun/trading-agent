"""Alert inserts. No alerting policy here."""

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.alerts.models import Alert, AlertAck, AlertKind, Severity


async def insert_alert(
    db: AsyncSession,
    *,
    kind: AlertKind,
    severity: Severity,
    message: str,
    details: dict[str, Any],
    dedupe_key: str | None,
    card_id: uuid.UUID | None,
    lot_id: uuid.UUID | None,
    order_id: uuid.UUID | None,
    message_id: int | None,
) -> uuid.UUID | None:
    """Insert unless `dedupe_key` already exists. Returns the new id, or None if deduped."""
    stmt = (
        insert(Alert)
        .values(
            alert_id=uuid.uuid4(),
            kind=kind,
            severity=severity,
            message=message,
            details=details,
            dedupe_key=dedupe_key,
            card_id=card_id,
            lot_id=lot_id,
            order_id=order_id,
            message_id=message_id,
        )
        .on_conflict_do_nothing()
        .returning(col(Alert.alert_id))
    )
    alert_id: uuid.UUID | None = await db.scalar(stmt)
    return alert_id


async def insert_ack(db: AsyncSession, alert_id: uuid.UUID, session_id: uuid.UUID) -> bool:
    """False if the alert was already acknowledged."""
    stmt = (
        insert(AlertAck)
        .values(alert_id=alert_id, session_id=session_id)
        .on_conflict_do_nothing()
        .returning(col(AlertAck.alert_id))
    )
    return await db.scalar(stmt) is not None
