"""Web Push (FR-26, C58, C75): a bus subscriber that applies the push matrix and sends to every
active subscription. Disabled cards never push. Also stamps `stage_timings(pushed)` once a
`card.created` has been fanned out (PRD §12 per-stage timing)."""

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.alerts.models import Alert, AlertKind, Severity
from app.cards.models import Card, CardState
from app.config import Settings
from app.db import in_transaction
from app.execution.models import AutoReason, Order
from app.inbox.models import Stage, StageTiming
from app.interpret.models import Signal
from app.notify.bus import Event, EventBus
from app.notify.models import PushSubscription

logger = logging.getLogger(__name__)
Sender = Callable[[dict[str, Any], str], object]  # (subscription_info, json data)
GONE = {404, 410}


def should_push(
    event_type: str,
    *,
    card_state: str | None = None,
    alert_severity: str | None = None,
    alert_kind: str | None = None,
    auto_reason: str | None = None,
) -> bool:
    """The FR-26 push matrix."""
    match event_type:
        case "card.created":
            return card_state == CardState.PENDING  # disabled cards never push (C75)
        case "fill.created":
            return True
        case "order.created":
            return auto_reason is not None and auto_reason != AutoReason.REPRICE
        case "alert.created":
            return alert_severity in (Severity.URGENT, Severity.WARNING) or (
                alert_kind == AlertKind.RISK_LIMIT
            )
    return False


class PushService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        bus: EventBus,
        settings: Settings,
        sender: Sender | None = None,
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._settings = settings
        self._sender = sender or self._webpush
        self.enabled = sender is not None or (
            settings.vapid_private_key is not None and settings.vapid_subject is not None
        )

    async def run(self) -> None:
        async for event in self._bus.stream():
            try:
                await self.handle(event)
            except Exception:
                logger.exception("push handling failed", extra={"event": event.type})

    async def handle(self, event: Event) -> int:
        """Returns the number of pushes sent."""
        message = await self._message(event)
        if message is None or not self.enabled:
            return 0
        return await self._send_all(message)

    async def _message(self, event: Event) -> dict[str, str] | None:
        async with self._sessions() as db:
            if event.type == "card.created":
                card = await db.get(Card, uuid.UUID(event.id))
                if card is None:
                    return None
                await self._stamp_pushed(card)
                if should_push(event.type, card_state=card.state):
                    return {"title": f"New {card.action} card", "url": f"/cards/{card.card_id}"}
            elif event.type == "alert.created":
                alert = await db.get(Alert, uuid.UUID(event.id))
                if alert is not None and should_push(
                    event.type, alert_severity=alert.severity, alert_kind=alert.kind
                ):
                    return {"title": alert.message, "url": "/alerts"}
            elif event.type == "order.created":
                order = await db.get(Order, uuid.UUID(event.id))
                if order is not None and should_push(event.type, auto_reason=order.auto_reason):
                    return {
                        "title": f"Automatic {order.auto_reason}",
                        "url": f"/orders/{order.order_id}",
                    }
            elif should_push(event.type):
                return {"title": "Fill", "url": "/orders"}
        return None

    async def _send_all(self, message: dict[str, str]) -> int:
        async with self._sessions() as db:
            subs = (
                await db.scalars(
                    select(PushSubscription).where(col(PushSubscription.revoked_at).is_(None))
                )
            ).all()
        data, sent = json.dumps(message), 0
        for sub in subs:
            info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
            try:
                await asyncio.to_thread(self._sender, info, data)
                sent += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in GONE:  # the browser dropped the subscription
                    await self._revoke(sub.subscription_id)
                else:
                    logger.warning("push failed", extra={"status": status})
        return sent

    async def _revoke(self, subscription_id: uuid.UUID) -> None:
        async with in_transaction(self._sessions, None) as db:
            await db.execute(
                update(PushSubscription)
                .where(col(PushSubscription.subscription_id) == subscription_id)
                .values(revoked_at=datetime.now(UTC))
            )

    async def _stamp_pushed(self, card: Card) -> None:
        if card.signal_id is None:
            return
        async with in_transaction(self._sessions, None) as db:
            signal = await db.get(Signal, card.signal_id)
            if signal is None:
                return
            attempt = await db.scalar(
                select(col(StageTiming.attempt))
                .where(
                    col(StageTiming.message_id) == signal.message_id,
                    col(StageTiming.event_type) == signal.event_type,
                    col(StageTiming.event_seq) == signal.event_seq,
                    col(StageTiming.stage) == Stage.CARD,
                )
                .order_by(col(StageTiming.attempt).desc())
                .limit(1)
            )
            await db.execute(
                pg_insert(StageTiming)
                .values(
                    message_id=signal.message_id,
                    event_type=signal.event_type,
                    event_seq=signal.event_seq,
                    attempt=attempt or 0,
                    stage=Stage.PUSHED,
                )
                .on_conflict_do_nothing()
            )

    def _webpush(self, info: dict[str, Any], data: str) -> object:
        key = self._settings.vapid_private_key
        assert key is not None and self._settings.vapid_subject  # noqa: S101 — enabled check
        return webpush(
            subscription_info=info,
            data=data,
            vapid_private_key=key.get_secret_value(),
            vapid_claims={"sub": self._settings.vapid_subject},
            timeout=10,
        )
