"""Per-provider sequencing (FR-1a, TRD §2.4): one worker per provider, post-time order,
providers in parallel, poison never blocks a provider.

ponytail: single process (asyncio lock per provider). Multi-process upgrade: session-level
pg_try_advisory_lock per provider on a dedicated connection (database-schema.md §8).
"""

import asyncio
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction, listen_forever
from app.dispatcher.classify import age_seconds, error_summary, is_stale, skip_reason
from app.dispatcher.types import PoisonHandler, ProcessingContext, Processor
from app.inbox import repo as inbox
from app.inbox.models import InboxStatus
from app.logging import bind
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        config: ConfigService,
        processor: Processor,
        poison: PoisonHandler,
        clock: Clock,
        listen_dsn: str | None = None,
        idle_exit_s: float = 60.0,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._processor = processor
        self._poison = poison
        self._clock = clock
        self._listen_dsn = listen_dsn
        self._idle_exit_s = idle_exit_s
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._wakeups: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)
        self._slots: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._listener: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """TRD §9 step 6: re-queue events a crash left `processing`, then start workers."""
        async with in_transaction(self._sessions, None) as db:
            reset = await inbox.reset_in_flight(db)
        if reset:
            logger.warning("re-queued in-flight events after restart", extra={"count": reset})
        if self._listen_dsn is not None:
            self._listener = asyncio.create_task(
                listen_forever(self._listen_dsn, "inbox", self._on_notify, self.discover),
                name="inbox-listener",
            )
        else:
            await self.discover()

    async def stop(self) -> None:
        tasks = [*self._workers.values(), *([self._listener] if self._listener else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    async def discover(self) -> None:
        """Wake a worker for every provider with waiting events."""
        async with in_transaction(self._sessions, None) as db:
            providers = await inbox.providers_waiting(db)
        for provider_id in providers:
            self.wake(provider_id)

    def wake(self, provider_id: int) -> None:
        self._wakeups[provider_id].set()
        task = self._workers.get(provider_id)
        if task is None or task.done():
            self._workers[provider_id] = asyncio.create_task(
                self._worker(provider_id), name=f"provider-{provider_id}"
            )

    def slot(self, provider_id: int) -> asyncio.Lock:
        """The provider slot: held for a whole `process()` call (AC-18)."""
        return self._slots[provider_id]

    def busy(self) -> bool:
        return any(not task.done() for task in self._workers.values())

    async def _on_notify(self, _channel: str, payload: str) -> None:
        self.wake(int(payload))

    async def _worker(self, provider_id: int) -> None:
        wakeup = self._wakeups[provider_id]
        while True:
            wakeup.clear()  # before the check, so a NOTIFY during the check is not lost
            if await self._process_next(provider_id):
                continue
            try:
                await asyncio.wait_for(wakeup.wait(), self._idle_exit_s)
            except TimeoutError:
                self._workers.pop(provider_id, None)  # no await between here and return
                return

    async def _process_next(self, provider_id: int) -> bool:
        """Claim and handle one event. False when the provider has nothing waiting."""
        async with in_transaction(self._sessions, None) as db:
            env = await inbox.next_for_provider(db, provider_id)
            if env is None:
                return False
            attempt = await inbox.claim(db, env.key, self._clock.now())
            late = await inbox.later_applied(db, provider_id, env.posted_at)
        config = self._config.get() or RuntimeConfig()
        with bind(provider_id=provider_id, message_id=env.key.message_id):
            if (reason := skip_reason(env, config)) is not None:
                async with in_transaction(self._sessions, None) as db:
                    await inbox.mark(db, env.key, InboxStatus.DONE, skip_reason=reason)
                return True
            started_at = self._clock.now()
            age_s = age_seconds(started_at, env.posted_at)
            ctx = ProcessingContext(
                envelope=env,
                attempt=attempt,
                started_at=started_at,
                age_s=age_s,
                stale=is_stale(age_s, config),
                late=late,
            )
            try:
                async with self.slot(provider_id):
                    await self._processor.process(ctx)
            except Exception as exc:
                await self._failed(ctx, exc, config)
        return True

    async def _failed(self, ctx: ProcessingContext, exc: Exception, config: RuntimeConfig) -> None:
        error = error_summary(exc)
        poisoned = ctx.attempt >= config.poison_retry_limit
        logger.exception("processing failed", extra={"attempt": ctx.attempt, "poison": poisoned})
        status = InboxStatus.FAILED if poisoned else InboxStatus.RECEIVED  # retry same event
        async with in_transaction(self._sessions, None) as db:
            await inbox.mark(db, ctx.envelope.key, status, error=error)
        if poisoned:
            try:
                await self._poison.on_poison(ctx, error)
            except Exception:
                logger.exception("poison handler failed")


class AlertPoisonHandler:
    """FR-1e: alert now; F026 adds the raw review card."""

    def __init__(self, alerts: AlertService) -> None:
        self._alerts = alerts

    async def on_poison(self, ctx: ProcessingContext, error: str) -> None:
        key = ctx.envelope.key
        await self._alerts.raise_(
            AlertKind.POISON,
            Severity.WARNING,
            f"Message could not be processed after {ctx.attempt} attempts; needs review",
            details={"error": error, "event_type": key.event_type, "event_seq": key.event_seq},
            dedupe_key=f"poison:{key.message_id}:{key.event_type}:{key.event_seq}",
            message_id=key.message_id,
        )
