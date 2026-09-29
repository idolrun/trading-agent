"""Read-only SQL for the PRD §16 metrics. Everything is computed from the audit tables."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import AlertKind
from app.api.schemas.health import (
    CardOutcome,
    LlmModelStats,
    MessageCounts,
    OrderStats,
    PostgresStats,
    QueueWait,
    StageLatency,
)
from app.inbox.models import Stage


async def _rows(db: AsyncSession, sql: str, **params: Any) -> list[Any]:
    return list((await db.execute(text(sql), params)).mappings().all())


def _ms(value: float | None) -> float:
    return round(value or 0.0, 1)


async def stage_latency(db: AsyncSession, since: datetime) -> list[StageLatency]:
    """Per stage: time since the previous stage of the message's final attempt (attempt 0 =
    `received`). `total` = received -> pushed. Slow path = the extractor ran for the message."""
    rows = await _rows(
        db,
        """
        WITH msgs AS (
            SELECT message_id, event_type, event_seq FROM stage_timings
            WHERE stage = 'received' AND at >= :since
        ), st AS (
            SELECT t.*, max(t.attempt) OVER (
                PARTITION BY t.message_id, t.event_type, t.event_seq) AS last_attempt
            FROM stage_timings t JOIN msgs m USING (message_id, event_type, event_seq)
        ), d AS (
            SELECT stage,
                extract(epoch FROM at - lag(at) OVER w) * 1000 AS ms,
                extract(epoch FROM at - first_value(at) OVER w) * 1000 AS total_ms,
                EXISTS (SELECT 1 FROM model_calls m WHERE m.role = 'extract'
                        AND m.message_id = st.message_id
                        AND m.event_type = st.event_type
                        AND m.event_seq = st.event_seq) AS slow
            FROM st WHERE attempt = 0 OR attempt = last_attempt
            WINDOW w AS (PARTITION BY message_id, event_type, event_seq
                         ORDER BY attempt, array_position(CAST(:stages AS text[]), stage))
        ), points AS (
            SELECT stage, ms, slow FROM d WHERE stage <> 'received'
            UNION ALL SELECT 'total', total_ms, slow FROM d WHERE stage = 'pushed'
        )
        SELECT stage, slow, count(*) AS n,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY ms) AS p99
        FROM points GROUP BY stage, slow
        """,
        since=since,
        stages=[s.value for s in Stage],
    )
    order = [*(s.value for s in Stage), "total"]
    rows.sort(key=lambda r: (order.index(r["stage"]), r["slow"]))
    return [
        StageLatency(
            stage=r["stage"],
            path="slow" if r["slow"] else "fast",
            count=r["n"],
            p50_ms=_ms(r["p50"]),
            p95_ms=_ms(r["p95"]),
            p99_ms=_ms(r["p99"]),
        )
        for r in rows
    ]


async def queue_wait(db: AsyncSession, since: datetime) -> list[QueueWait]:
    """received -> first dequeue, per provider."""
    rows = await _rows(
        db,
        """
        SELECT i.provider_id, count(*) AS n,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY w.ms) AS p95, max(w.ms) AS max
        FROM (
            SELECT r.message_id, r.event_type, r.event_seq,
                extract(epoch FROM d.at - r.at) * 1000 AS ms
            FROM stage_timings r JOIN stage_timings d USING (message_id, event_type, event_seq)
            WHERE r.stage = 'received' AND r.attempt = 0 AND r.at >= :since
              AND d.stage = 'dequeued' AND d.attempt = 1
        ) w JOIN inbox i USING (message_id, event_type, event_seq)
        GROUP BY i.provider_id ORDER BY i.provider_id
        """,
        since=since,
    )
    return [
        QueueWait(
            provider_id=r["provider_id"], count=r["n"], p95_ms=_ms(r["p95"]), max_ms=_ms(r["max"])
        )
        for r in rows
    ]


async def message_counts(db: AsyncSession, since: datetime, duplicates: int) -> MessageCounts:
    r = (
        await _rows(
            db,
            """
            SELECT count(*) AS total, count(*) FILTER (WHERE late) AS late,
                count(*) FILTER (WHERE status = 'stale') AS stale,
                count(*) FILTER (WHERE backfilled) AS backfilled,
                count(*) FILTER (WHERE status = 'failed') AS failed
            FROM inbox WHERE posted_at >= :since
            """,
            since=since,
        )
    )[0]
    return MessageCounts(**r, duplicates_since_start=duplicates)


async def llm_models(db: AsyncSession, since: datetime) -> list[LlmModelStats]:
    stats = await _rows(
        db,
        """
        SELECT provider, model, count(*) AS calls, count(*) FILTER (WHERE attempt > 1) AS fb,
            coalesce(sum(cost_usd), 0) AS spend,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
        FROM model_calls WHERE started_at >= :since AND outcome <> 'circuit_open'
        GROUP BY provider, model ORDER BY provider, model
        """,
        since=since,
    )
    outcomes: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for r in await _rows(
        db,
        """
        SELECT provider, model, outcome, count(*) AS n FROM model_calls
        WHERE started_at >= :since GROUP BY provider, model, outcome
        """,
        since=since,
    ):
        outcomes[r["provider"], r["model"]][r["outcome"]] = r["n"]
    return [
        LlmModelStats(
            provider=r["provider"],
            model=r["model"],
            calls=r["calls"],
            latency_p50_ms=_ms(r["p50"]),
            latency_p95_ms=_ms(r["p95"]),
            outcomes=(o := outcomes[r["provider"], r["model"]]),
            error_rate=round(1 - o.get("ok", 0) / r["calls"], 4),
            fallback_calls=r["fb"],
            spend_usd=r["spend"],
        )
        for r in stats
    ]


async def slow_path_share(db: AsyncSession, since: datetime) -> float | None:
    r = (
        await _rows(
            db,
            """
            SELECT count(DISTINCT (message_id, event_type, event_seq))
                       FILTER (WHERE role = 'extract') AS slow,
                   count(DISTINCT (message_id, event_type, event_seq)) AS total
            FROM model_calls
            WHERE started_at >= :since AND message_id IS NOT NULL
              AND role IN ('interpret', 'extract')
            """,
            since=since,
        )
    )[0]
    return round(r["slow"] / r["total"], 4) if r["total"] else None


async def month_spend(db: AsyncSession, month_start: datetime) -> Decimal:
    spend: Decimal = await db.scalar(
        text("SELECT coalesce(sum(cost_usd), 0) FROM model_calls WHERE started_at >= :s"),
        {"s": month_start},
    )
    return spend


async def card_outcomes(db: AsyncSession, since: datetime) -> list[CardOutcome]:
    rows = await _rows(
        db,
        """
        SELECT to_state, reason_code, count(*) AS n FROM card_events
        WHERE at >= :since AND to_state <> 'pending'
        GROUP BY to_state, reason_code ORDER BY to_state, reason_code
        """,
        since=since,
    )
    return [
        CardOutcome(to_state=r["to_state"], reason_code=r["reason_code"], count=r["n"])
        for r in rows
    ]


async def order_stats(db: AsyncSession, since: datetime) -> OrderStats:
    rows = await _rows(
        db,
        """
        SELECT status, count(*) AS n, count(*) FILTER (WHERE auto_reason = 'reprice') AS reprices
        FROM orders WHERE created_at >= :since GROUP BY status
        """,
        since=since,
    )
    by_status = {r["status"]: r["n"] for r in rows}
    return OrderStats(
        by_status=by_status,
        rejected=by_status.get("rejected", 0),
        reprices=sum(r["reprices"] for r in rows),
    )


async def alert_counts(db: AsyncSession, since: datetime) -> dict[str, int]:
    """Alerts in the window by kind (invariant blocks, reconciliation mismatches)."""
    rows = await _rows(
        db,
        """
        SELECT kind, count(*) AS n FROM alerts
        WHERE created_at >= :since AND kind = ANY(CAST(:kinds AS text[])) GROUP BY kind
        """,
        since=since,
        kinds=[AlertKind.INVARIANT_BLOCK.value, AlertKind.RECONCILE_MISMATCH.value],
    )
    return {r["kind"]: r["n"] for r in rows}


async def postgres_stats(db: AsyncSession) -> PostgresStats:
    """Needs no `pg_monitor`: counting rows and ungranted locks is allowed to every role."""
    r = (
        await _rows(
            db,
            """
            SELECT (SELECT count(*) FROM pg_stat_activity
                    WHERE datname = current_database()) AS connections,
                   (SELECT count(*) FROM pg_locks WHERE NOT granted) AS lock_waits
            """,
        )
    )[0]
    return PostgresStats(**r)
