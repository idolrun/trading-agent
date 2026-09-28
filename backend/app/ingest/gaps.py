"""Gap detection and backfill (FR-1d, AC-21): fetch channel history the webhook may have
missed and feed it through `inbox.accept()` tagged `backfilled` (duplicates are no-ops).

Each channel keeps a watermark: the newest message id already verified against history.
A run fetches everything after it, so gaps anywhere after the watermark are found — not only
after the newest stored message. On the first run of a process the watermark is the newest
stored message posted before `now - lookback`, so a restart re-verifies that window.
ponytail: watermark in memory; persist it if startup re-verification gets too costly.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.inbox import repo as inbox
from app.ingest.adapter import UnmappablePayload, to_envelope
from app.markettime import Clock, is_regular_hours
from app.messageapi.port import MessageHistory, MessageHistoryUnavailable
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService

logger = logging.getLogger(__name__)


class GapDetector:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        history: MessageHistory,
        config: ConfigService,
        alerts: AlertService,
        clock: Clock,
        page_size: int = 100,
        lookback: timedelta = timedelta(hours=8),  # one full session (C53)
    ) -> None:
        self._sessions = session_factory
        self._history = history
        self._config = config
        self._alerts = alerts
        self._clock = clock
        self._page_size = page_size
        self._lookback = lookback
        self._watermarks: dict[int, int | None] = {}
        self._lock = asyncio.Lock()

    async def run_once(self) -> int:
        """Backfill every configured channel. Returns how many new events were accepted."""
        async with self._lock:  # no overlapping runs
            channels = (self._config.get() or RuntimeConfig()).channels
            fresh = [c for c in channels if c not in self._watermarks]
            if fresh:
                async with in_transaction(self._sessions, None) as db:
                    seen = await inbox.last_seen_per_channel(
                        db, fresh, self._clock.now() - self._lookback
                    )
                for channel in fresh:
                    self._watermarks[channel] = seen.get(channel)
            accepted = 0
            for channel in channels:
                try:
                    accepted += await self._backfill(channel)
                except MessageHistoryUnavailable as exc:
                    await self._history_down(channel, exc)
            if accepted:
                logger.warning("backfilled missed messages", extra={"count": accepted})
            return accepted

    async def run(self, interval_s: float = 60.0) -> None:
        """Periodic trigger during regular hours (TRD §2.4)."""
        while True:
            if is_regular_hours(self._clock.now()):
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("gap detection failed")
            await asyncio.sleep(interval_s)

    async def _backfill(self, channel: int) -> int:
        accepted = 0
        while page := await self._history.fetch_after(
            channel, self._watermarks[channel], self._page_size
        ):
            for payload in page:
                accepted += await self._accept(payload)
            newest = max(_message_id(p) for p in page)
            if newest <= (self._watermarks[channel] or 0):
                break  # history did not advance; avoid looping on a bad page
            self._watermarks[channel] = newest
        return accepted

    async def _accept(self, payload: dict[str, Any]) -> int:
        try:
            env = to_envelope(payload, self._clock.now()).model_copy(update={"backfilled": True})
        except UnmappablePayload as exc:
            await self._alerts.raise_(
                AlertKind.MESSAGE_API,
                Severity.URGENT,
                "Backfilled message could not be mapped (contract changed?)",
                details={"reason": str(exc)[:500]},
                dedupe_key=f"unmappable:history:{_message_id(payload)}",
            )
            return 0
        async with in_transaction(self._sessions, None) as db:
            return int(await inbox.accept(db, env))

    async def _history_down(self, channel: int, exc: Exception) -> None:
        logger.warning("message history unavailable", extra={"channel_id": channel})
        await self._alerts.raise_(
            AlertKind.MESSAGE_API,
            Severity.WARNING,
            "Message history unavailable; gap backfill will retry",
            details={"channel_id": str(channel), "error": str(exc)[:300]},
            dedupe_key=f"history_unavailable:{channel}:{self._clock.now():%Y%m%d%H}",
        )


def _message_id(payload: dict[str, Any]) -> int:
    try:
        return int(payload["message"]["id"])
    except (KeyError, TypeError, ValueError):
        return 0
