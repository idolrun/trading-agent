"""Poison handling for the dispatcher (FR-1e). The per-event pipeline is the message graph
(`app/graph`, F035); planning and apply are the libraries `pipeline.plan` / `pipeline.apply`."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.dispatcher.types import ProcessingContext
from app.pipeline.apply import apply_raw_review


class RawReviewPoisonHandler:
    """FR-1e: after `poison_retry_limit` failures: alert + raw review card; the provider's
    next message still processes (the dispatcher already marked this one `failed`)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], alerts: AlertService) -> None:
        self._sessions = sessions
        self._alerts = alerts

    async def on_poison(self, ctx: ProcessingContext, error: str) -> None:
        key = ctx.envelope.key
        async with in_transaction(self._sessions, None) as db:
            card_id = await apply_raw_review(db, ctx, "poison", mark=None)
            await self._alerts.raise_(
                AlertKind.POISON,
                Severity.WARNING,
                f"Message could not be processed after {ctx.attempt} attempts; needs review",
                details={"error": error, "event_type": key.event_type, "event_seq": key.event_seq},
                dedupe_key=f"poison:{key.message_id}:{key.event_type}:{key.event_seq}",
                card_id=card_id,
                message_id=key.message_id,
                db=db,
            )
