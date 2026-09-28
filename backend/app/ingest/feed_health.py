"""Feed silence (FR-4, AC-8) and backlog catch-up (FR-1c) detection. Never guesses a missing
threshold: an unset one is reported once and its check is skipped."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction
from app.inbox import repo as inbox
from app.inbox.repo import Backlog
from app.markettime import Clock, is_regular_hours
from app.runtime_config.models import Actor
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.flags import FlagService
from app.system.models import FlagName

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedState:
    # silent = in regular hours with no signal for > feed_silence_s; hold = quiet outside
    # hours (no change); None = check not configured.
    feed: Literal["silent", "ok", "hold"] | None
    catch_up: bool | None  # None = check not configured
    gap_s: float


def evaluate(
    now: datetime,
    last_signal: datetime,
    backlog: Backlog,
    config: RuntimeConfig,
    in_regular_hours: bool,
) -> FeedState:
    gap_s = (now - last_signal).total_seconds()
    feed: Literal["silent", "ok", "hold"] | None = None
    if config.feed_silence_s is not None:
        if gap_s <= config.feed_silence_s:
            feed = "ok"
        else:
            feed = "silent" if in_regular_hours else "hold"
    catch_up = None
    if config.catch_up_backlog_n is not None:
        oldest_age = (
            (now - backlog.oldest_posted_at).total_seconds() if backlog.oldest_posted_at else 0
        )
        catch_up = backlog.count > config.catch_up_backlog_n or oldest_age > config.stale_entry_s
    return FeedState(feed=feed, catch_up=catch_up, gap_s=gap_s)


class FeedHealthMonitor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        config: ConfigService,
        flags: FlagService,
        alerts: AlertService,
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._flags = flags
        self._alerts = alerts
        self._clock = clock
        self._started_at = clock.now()
        # Grace period: the feed counts as heard at process start.
        self.last_message_at = self._started_at
        self.last_heartbeat_at = self._started_at
        self.on_recovered: list[Callable[[], Awaitable[None]]] = []  # e.g. gap backfill (F014)

    def note_message(self, at: datetime) -> None:
        self.last_message_at = max(self.last_message_at, at)

    def note_heartbeat(self, at: datetime) -> None:
        self.last_heartbeat_at = max(self.last_heartbeat_at, at)

    async def tick(self) -> FeedState:
        now = self._clock.now()
        config = self._config.get() or RuntimeConfig()
        async with in_transaction(self._sessions, None) as db:
            backlog = await inbox.backlog(db)
        last_signal = max(self.last_message_at, self.last_heartbeat_at)
        state = evaluate(now, last_signal, backlog, config, is_regular_hours(now))

        if state.feed is None:
            await self._unconfigured(AlertKind.FEED_SILENCE, "feed_silence_s")
        elif state.feed == "silent":
            reason = f"no message or heartbeat for {int(state.gap_s)} s during market hours"
            await self._flags.set(FlagName.FEED_DEGRADED, True, reason, Actor.SYSTEM)
        elif state.feed == "ok" and await self._flags.set(
            FlagName.FEED_DEGRADED, False, "feed resumed", Actor.SYSTEM
        ):
            for hook in self.on_recovered:
                try:
                    await hook()
                except Exception:
                    logger.exception("feed recovery hook failed")

        if state.catch_up is None:
            await self._unconfigured(AlertKind.CATCH_UP, "catch_up_backlog_n")
        elif state.catch_up:
            reason = f"backlog of {backlog.count} events waiting"
            await self._flags.set(FlagName.CATCH_UP, True, reason, Actor.SYSTEM)
        else:
            await self._flags.set(FlagName.CATCH_UP, False, "backlog drained", Actor.SYSTEM)
        return state

    async def run(self, interval_s: float = 1.0) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("feed health tick failed")
            await asyncio.sleep(interval_s)

    async def _unconfigured(self, kind: AlertKind, field: str) -> None:
        """Once per process: say which threshold is missing instead of guessing one."""
        await self._alerts.raise_(
            kind,
            Severity.WARNING,
            f"Feed health check skipped: runtime config `{field}` is not set",
            details={"field": field},
            dedupe_key=f"unconfigured:{field}:{self._started_at.isoformat()}",
        )
