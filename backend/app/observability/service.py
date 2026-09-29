"""Health metrics (PRD §16), the `/health` check, and the two daily maintenance jobs."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.models import AlertKind, Severity
from app.alerts.service import AlertService
from app.api.schemas.health import (
    HealthDetails,
    LlmProviderStats,
    LlmStats,
    ReconciliationStats,
    Window,
)
from app.db import in_transaction
from app.inbox import repo as inbox
from app.ingest.feed_health import FeedHealthMonitor
from app.llm.chain import LlmChain
from app.markettime import Clock
from app.observability import queries
from app.observability.counters import counts, per_provider
from app.reconcile.service import Reconciler
from app.runtime_config.schema import RuntimeConfig
from app.runtime_config.service import ConfigService
from app.system.heartbeat import last_beat
from app.system.models import FlagName, SystemFlag

logger = logging.getLogger(__name__)

DEGRADING = (FlagName.FEED_DEGRADED, FlagName.PARSER_DEGRADED, FlagName.ENTRIES_BLOCKED)
HEARTBEAT_MAX_AGE = timedelta(seconds=15)  # TRD §2.4
CHECKPOINT_RETENTION = timedelta(days=30)  # TRD §5.2
SPEND_THRESHOLDS = ((Decimal("1.0"), Severity.URGENT), (Decimal("0.8"), Severity.WARNING))
DAY_S = 86_400.0


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class HealthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock,
        config: ConfigService,
        feed: FeedHealthMonitor | None = None,
        llm: LlmChain | None = None,
        reconciler: Reconciler | None = None,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock
        self._config = config
        self._feed = feed
        self._llm = llm
        self._reconciler = reconciler
        self._started_at = clock.now()

    async def alive(self, *, require_heartbeat: bool) -> str | None:
        """`/health`: None when healthy, else why not. The DB must answer, and (with background
        services) the dispatcher heartbeat must be at most 15 s old."""
        try:
            async with self._sessions() as db:
                beat = await last_beat(db)
        except Exception:
            logger.exception("health check: database unreachable")
            return "database unreachable"
        if require_heartbeat and (beat is None or self._clock.now() - beat > HEARTBEAT_MAX_AGE):
            return "dispatcher heartbeat stale"
        return None

    async def details(self, window: Window) -> HealthDetails:
        now = self._clock.now()
        since = {
            "session": self._started_at,
            "day": now - timedelta(days=1),
            "7d": now - timedelta(days=7),
        }[window]
        async with self._sessions() as db:
            flags = {f.flag: f.active for f in await db.scalars(select(SystemFlag))}
            backlog = await inbox.backlog(db)
            beat = await last_beat(db)
            alerts = await queries.alert_counts(db, since)
            spend = await queries.month_spend(db, _month_start(now))
            budget = (self._config.get() or RuntimeConfig()).llm_monthly_budget_usd
            return HealthDetails(
                status="degraded" if any(flags.get(f) for f in DEGRADING) else "ok",
                window=window,
                feed_silence_seconds=(now - self._feed.last_message_at).total_seconds()
                if self._feed
                else None,
                feed_heartbeat_age_seconds=(now - self._feed.last_heartbeat_at).total_seconds()
                if self._feed
                else None,
                flags=flags,
                catch_up=bool(flags.get(FlagName.CATCH_UP)),
                backlog_depth=backlog.count,
                oldest_unprocessed_seconds=(now - backlog.oldest_posted_at).total_seconds()
                if backlog.oldest_posted_at
                else None,
                heartbeat_age_seconds=(now - beat).total_seconds() if beat else None,
                latency=await queries.stage_latency(db, since),
                queue_wait=await queries.queue_wait(db, since),
                messages=await queries.message_counts(db, since, counts["duplicate"]),
                llm=LlmStats(
                    models=await queries.llm_models(db, since),
                    providers=self._providers(),
                    slow_path_share=await queries.slow_path_share(db, since),
                    spend_month_to_date_usd=spend,
                    monthly_budget_usd=budget,
                ),
                card_outcomes=await queries.card_outcomes(db, since),
                orders=await queries.order_stats(db, since),
                invariant_blocks=alerts.get(AlertKind.INVARIANT_BLOCK, 0),
                reconciliation=self._reconciliation(alerts.get(AlertKind.RECONCILE_MISMATCH, 0)),
                postgres=await queries.postgres_stats(db),
            )

    def _providers(self) -> list[LlmProviderStats]:
        breakers = self._llm.breakers_open() if self._llm else {}
        slots, waits, limited = (
            per_provider("slot"),
            per_provider("slot_waited"),
            per_provider("rate_limited"),
        )
        return [
            LlmProviderStats(
                provider=p,
                breaker_open=breakers.get(p),
                slots_since_start=slots.get(p, 0),
                semaphore_waits_since_start=waits.get(p, 0),
                rate_limited_since_start=limited.get(p, 0),
            )
            for p in sorted(breakers.keys() | slots.keys() | limited.keys())
        ]

    def _reconciliation(self, mismatch_alerts: int) -> ReconciliationStats:
        last = self._reconciler.last if self._reconciler else None
        return ReconciliationStats(
            last_run_at=last.at if last else None,
            clean=last.clean if last else None,
            mismatches=len(last.mismatches) if last else 0,
            unknown_orders=len(last.unknown_orders) if last else 0,
            error=last.error if last else None,
            mismatch_alerts=mismatch_alerts,
        )


async def check_spend(
    sessions: async_sessionmaker[AsyncSession],
    *,
    config: ConfigService,
    alerts: AlertService,
    clock: Clock,
) -> None:
    """Month-to-date spend vs the budget: warning at 80 %, urgent at 100 % (no shutdown, C44).
    One alert per level and month: the alert's `dedupe_key` is the memory, so restarts are safe."""
    budget = (config.get() or RuntimeConfig()).llm_monthly_budget_usd
    if budget is None:
        return
    now = clock.now()
    async with in_transaction(sessions, None) as db:
        spend = await queries.month_spend(db, _month_start(now))
    for fraction, severity in SPEND_THRESHOLDS:
        if spend >= budget * fraction:
            await alerts.raise_(
                AlertKind.LLM_SPEND,
                severity,
                f"LLM spend ${spend:.2f} is {int(fraction * 100)}% of the ${budget} monthly budget",
                details={"spend_usd": str(spend), "budget_usd": str(budget)},
                dedupe_key=f"llm_spend:{now:%Y-%m}:{severity}",
            )
            return


PRUNE_SQL = """
WITH old AS (
    SELECT c.thread_id FROM langgraph.checkpoints c
    JOIN inbox i ON regexp_replace(c.thread_id, ':[0-9]+$', '')
        = 'msg:' || i.message_id || ':' || i.event_type || ':' || i.event_seq
    WHERE i.status IN ('done', 'failed', 'stale') AND i.finished_at < :cutoff
    UNION
    SELECT c.thread_id FROM langgraph.checkpoints c
    JOIN inbox i ON regexp_replace(c.thread_id, ':[^:]+$', '') = 'recheck:' || i.message_id
    WHERE i.finished_at < :cutoff
    UNION
    SELECT c.thread_id FROM langgraph.checkpoints c
    JOIN cards k ON c.thread_id = 'card:' || k.card_id
    WHERE k.state NOT IN ('pending', 'approved')
      AND (SELECT max(e.at) FROM card_events e WHERE e.card_id = k.card_id) < :cutoff
), w AS (
    DELETE FROM langgraph.checkpoint_writes WHERE thread_id IN (SELECT thread_id FROM old)
), b AS (
    DELETE FROM langgraph.checkpoint_blobs WHERE thread_id IN (SELECT thread_id FROM old)
)
DELETE FROM langgraph.checkpoints WHERE thread_id IN (SELECT thread_id FROM old)
"""


async def prune_checkpoints(sessions: async_sessionmaker[AsyncSession], clock: Clock) -> int:
    """Delete LangGraph checkpoints of threads finished > 30 days ago: message threads (inbox
    row terminal) and approval threads (card no longer pending/approved). The domain tables
    stay the audit (TRD §5.2). Returns the number of checkpoints removed."""
    async with in_transaction(sessions, None) as db:
        result = await db.execute(text(PRUNE_SQL), {"cutoff": clock.now() - CHECKPOINT_RETENTION})
    return int(result.rowcount)  # type: ignore[attr-defined]


async def run_daily(
    name: str, job: Callable[[], Awaitable[object]], interval_s: float = DAY_S
) -> None:
    """Background task: a failing run is logged and retried at the next interval."""
    while True:
        try:
            await job()
        except Exception:
            logger.exception("daily job failed", extra={"job": name})
        await asyncio.sleep(interval_s)
