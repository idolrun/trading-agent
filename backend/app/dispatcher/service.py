"""Per-lane sequencing (FR-1a, TRD §2.4): one worker per lane = (channel, provider), post-time
order, lanes in parallel, poison never blocks a lane. One author in two channels is two lanes:
their contexts and books are separate (F068), so they need no common order.

ponytail: single process (asyncio lock per lane; `app.system.singleton` refuses a second
instance). Multi-process upgrade: session-level pg_try_advisory_lock per lane on a dedicated
connection (database-schema.md §8).
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack

import psycopg
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.db import in_transaction, listen_forever
from app.dispatcher.classify import age_seconds, error_summary, is_stale, skip_reason
from app.dispatcher.types import PoisonHandler, ProcessingContext, Processor
from app.inbox import repo as inbox
from app.inbox.envelope import EventKey, Lane
from app.inbox.models import InboxStatus, SkipReason
from app.logging import bind
from app.markettime import Clock
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService

logger = logging.getLogger(__name__)

# The database is unreachable (failover, restart, network): wait and retry, never poison.
# Driver errors only: a plain OSError (e.g. a TimeoutError from a broker call) is the message's.
CONNECTION_ERRORS = (psycopg.OperationalError, OperationalError, InterfaceError)
RETRY_MAX_S = 5.0
BACKFILL_WAIT_S = 1.0  # a backfilled event waiting for the gate re-checks for live ones


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
        on_recover: Callable[[], Awaitable[object]] | None = None,
        on_lane_idle: Callable[[Lane], Awaitable[object]] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._processor = processor
        self._poison = poison
        self._clock = clock
        self._listen_dsn = listen_dsn
        self._idle_exit_s = idle_exit_s
        self._workers: dict[Lane, asyncio.Task[None]] = {}
        self._wakeups: defaultdict[Lane, asyncio.Event] = defaultdict(asyncio.Event)
        self._slots: defaultdict[Lane, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Backfilled events share models and market data with live ones: one at a time
        # across all lanes, so history never takes more than one model slot.
        # ponytail: 1 global backfill slot; size it from config if backfill drains too slowly.
        self._backfill_gate = asyncio.Semaphore(1)
        self._listener: asyncio.Task[None] | None = None
        # TRD §9 after a failover: workers hold until `on_recover` (block entries, reconcile)
        # has run on the reconnected database.
        self._on_recover = on_recover
        # F089: runs under the lane slot each time the lane has nothing waiting (deferred
        # late re-links after a backfill). Must be cheap when there is nothing to do.
        self._on_lane_idle = on_lane_idle
        self._ready = asyncio.Event()
        self._ready.set()
        self._recovery: asyncio.Task[None] | None = None
        self._connected = False

    async def start(self) -> None:
        """TRD §9 step 6: re-queue events a crash left `processing`, then start workers."""
        async with in_transaction(self._sessions, None) as db:
            reset = await inbox.reset_in_flight(db)
        if reset:
            logger.warning("re-queued in-flight events after restart", extra={"count": reset})
        if self._listen_dsn is not None:
            self._listener = asyncio.create_task(
                listen_forever(self._listen_dsn, "inbox", self._on_notify, self._on_connect),
                name="inbox-listener",
            )
        else:
            await self.discover()

    async def stop(self) -> None:
        tasks = [
            *self._workers.values(),
            *([self._listener] if self._listener else []),
            *([self._recovery] if self._recovery else []),
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    async def discover(self) -> None:
        """Wake a worker for every lane with waiting events."""
        async with in_transaction(self._sessions, None) as db:
            lanes = await inbox.lanes_waiting(db)
        for lane in lanes:
            self.wake(lane)

    def wake(self, lane: Lane) -> None:
        self._wakeups[lane].set()
        task = self._workers.get(lane)
        if task is None or task.done():
            self._workers[lane] = asyncio.create_task(
                self._worker(lane), name=f"lane-{lane[0]}-{lane[1]}"
            )

    def slot(self, lane: Lane) -> asyncio.Lock:
        """The lane slot: held for a whole `process()` call (AC-18)."""
        return self._slots[lane]

    def busy(self) -> bool:
        return any(not task.done() for task in self._workers.values())

    async def _on_notify(self, _channel: str, payload: str) -> None:
        self.wake(inbox.parse_lane(payload))

    async def _on_connect(self) -> None:
        """First connect: pick up waiting events. A reconnect means the database went away
        (failover): recover before processing again.

        ponytail: an event claimed between the database's return and this reconnect (<= 1 s)
        is processed before recovery; a worker's own connection error closes that gap."""
        if self._connected:
            self.recover()
        else:
            self._connected = True
            await self.discover()

    def recover(self) -> None:
        """Hold every worker until the database answers and `on_recover` ran (idempotent)."""
        if self._recovery is not None and not self._recovery.done():
            return
        self._ready.clear()
        self._recovery = asyncio.create_task(self._recover(), name="dispatcher-recovery")

    async def _recover(self) -> None:
        """Retried until it completes: `on_recover` has blocked entries, so giving up (e.g. on
        a broker error) would leave them blocked and approved cards unresumed while workers
        run on."""
        delay = 0.5
        try:
            while True:
                try:
                    async with AsyncExitStack() as held:
                        for lane in sorted(self._slots):  # in-flight process() ends first
                            await held.enter_async_context(self._slots[lane])
                        if self._on_recover is not None:
                            await self._on_recover()
                    await self.discover()
                    logger.warning("dispatcher resumed after a database outage")
                    return
                except CONNECTION_ERRORS:
                    logger.warning("database unreachable; recovery retries", exc_info=True)
                except Exception:
                    logger.exception("failover recovery failed; retrying")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)
        finally:
            self._ready.set()

    async def _worker(self, lane: Lane) -> None:
        wakeup = self._wakeups[lane]
        while True:
            await self._ready.wait()
            wakeup.clear()  # before the check, so a NOTIFY during the check is not lost
            try:
                if await self._process_next(lane):
                    continue
            except CONNECTION_ERRORS:  # the claim failed: the event is still `received`
                logger.warning("database unreachable; worker waits", exc_info=True)
                self.recover()
                continue
            await self.idle(lane)
            try:
                await asyncio.wait_for(wakeup.wait(), self._idle_exit_s)
            except TimeoutError:
                self._workers.pop(lane, None)  # no await between here and return
                return

    async def idle(self, lane: Lane) -> None:
        if self._on_lane_idle is None:
            return
        try:
            async with self.slot(lane):
                await self._on_lane_idle(lane)
        except Exception:
            logger.exception("lane idle hook failed", extra={"lane": lane})

    async def _process_next(self, lane: Lane) -> bool:
        """Claim and handle one event. False when the lane has nothing waiting."""
        async with in_transaction(self._sessions, None) as db:
            env = await inbox.next_for_lane(db, lane)
        if env is None:
            return False
        config = self._config.get() or RuntimeConfig()
        if not env.backfilled or skip_reason(env, config) is not None:
            return await self._claim_and_process(lane, env.key, config)
        try:
            await asyncio.wait_for(self._backfill_gate.acquire(), BACKFILL_WAIT_S)
        except TimeoutError:
            return True  # re-peek: a live event that arrived meanwhile goes first
        try:
            return await self._claim_and_process(lane, env.key, config)
        finally:
            self._backfill_gate.release()

    async def _claim_and_process(self, lane: Lane, key: EventKey, config: RuntimeConfig) -> bool:
        async with in_transaction(self._sessions, None) as db:
            env = await inbox.next_for_lane(db, lane)
            if env is None or env.key != key:
                return True  # the lane changed while waiting: re-peek
            attempt = await inbox.claim(db, env.key, self._clock.now())
            late = await inbox.later_applied(db, lane, env.posted_at)
        with bind(provider_id=lane[1], channel_id=lane[0], message_id=env.key.message_id):
            if (reason := skip_reason(env, config)) is not None:
                await self._mark(env.key, InboxStatus.DONE, skip_reason=reason)
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
                await self._process_held(ctx)
            except Exception as exc:
                await self._failed(ctx, exc, config)
        return True

    async def _process_held(self, ctx: ProcessingContext) -> None:
        """`process()` under the lane slot, never while failover recovery runs. Recovery
        takes every slot, so a slot acquired before it started is waited for; one acquired
        after sees `_ready` cleared and lets go (waiting inside the slot would deadlock)."""
        slot = self.slot(ctx.envelope.lane)
        while True:
            await self._ready.wait()
            async with slot:
                if self._ready.is_set():
                    await self._processor.process(ctx)
                    return

    async def _failed(self, ctx: ProcessingContext, exc: Exception, config: RuntimeConfig) -> None:
        error = error_summary(exc)
        outage = isinstance(exc, CONNECTION_ERRORS)  # not the message's fault: no poison
        poisoned = ctx.attempt >= config.poison_retry_limit and not outage
        logger.exception("processing failed", extra={"attempt": ctx.attempt, "poison": poisoned})
        status = InboxStatus.FAILED if poisoned else InboxStatus.RECEIVED  # retry same event
        if not await self._mark(ctx.envelope.key, status, error=error):
            logger.warning("event already applied; the failure after apply is not retried")
            return
        if poisoned:
            try:
                await self._poison.on_poison(ctx, error)
            except Exception:
                logger.exception("poison handler failed")

    async def _mark(
        self,
        key: EventKey,
        status: InboxStatus,
        *,
        error: str | None = None,
        skip_reason: SkipReason | None = None,
    ) -> bool:
        """Release a claimed event. Retried until the database answers: giving up would leave
        it `processing` (only a restart re-queues those) while later events overtake it.
        False: the event was already `done`/`stale` and stays so."""
        delay = 0.5
        while True:
            try:
                async with in_transaction(self._sessions, None) as db:
                    return await inbox.mark(db, key, status, error=error, skip_reason=skip_reason)
            except CONNECTION_ERRORS:
                logger.warning("database unreachable; retrying the inbox mark", exc_info=True)
                self.recover()
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_S)


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
