"""Raise and acknowledge alerts. Push delivery listens on `NOTIFY alerts` (F029)."""

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import repo
from app.alerts.models import AlertKind, Severity
from app.db import in_transaction, notify

logger = logging.getLogger(__name__)

_LOG_LEVEL = {Severity.INFO: logging.INFO, Severity.WARNING: logging.WARNING}


class AlertAlreadyAcknowledged(Exception):
    pass


class AlertNotFound(Exception):
    pass


class AlertService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def raise_(
        self,
        kind: AlertKind,
        severity: Severity,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        card_id: uuid.UUID | None = None,
        lot_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        message_id: int | None = None,
        db: AsyncSession | None = None,
    ) -> uuid.UUID | None:
        """Record an alert (joins `db`'s transaction if given). None = deduped, nothing new."""
        async with in_transaction(self._sessions, db) as session:
            alert_id = await repo.insert_alert(
                session,
                kind=kind,
                severity=severity,
                message=message,
                details=details or {},
                dedupe_key=dedupe_key,
                card_id=card_id,
                lot_id=lot_id,
                order_id=order_id,
                message_id=message_id,
            )
            if alert_id is not None:
                await notify(session, "alerts", str(alert_id))
        if alert_id is not None:
            logger.log(
                _LOG_LEVEL.get(severity, logging.ERROR),
                message,
                extra={"alert_id": str(alert_id), "alert_kind": kind},
            )
        return alert_id

    async def acknowledge(self, alert_id: uuid.UUID, session_id: uuid.UUID) -> None:
        try:
            async with in_transaction(self._sessions, None) as session:
                if not await repo.insert_ack(session, alert_id, session_id):
                    raise AlertAlreadyAcknowledged(alert_id)
        except IntegrityError as exc:  # FK: unknown alert (or session)
            raise AlertNotFound(alert_id) from exc
