"""F049: SQL metrics over synthetic timings, spend alerts, checkpoint pruning, counters."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.models import Alert, AlertKind, Severity
from app.alerts.service import AlertService
from app.db import create_session_factory, in_transaction
from app.inbox import repo as inbox
from app.inbox.models import InboxStatus, Stage, StageTiming
from app.ingest.adapter import to_envelope
from app.llm.limiter import ProviderLimiter
from app.llm.models import ModelCall
from app.markettime import FixedClock
from app.observability import queries
from app.observability.counters import counts, per_provider
from app.observability.service import HealthService, check_spend, prune_checkpoints
from app.runtime_config.schema import RuntimeConfig
from tests.integration.conftest import _url
from tests.integration.schema.helpers import T0, add_inbox, add_message, add_signal
from tests.messages import ac18

NOW = T0 + timedelta(hours=1)


@pytest.fixture
async def factory(owner_engine: object) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_url("test_database_url"), poolclass=NullPool)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_counters() -> Iterator[None]:
    counts.clear()
    yield
    counts.clear()


def ms(n: int) -> timedelta:
    return timedelta(milliseconds=n)


async def add_timed_message(
    db: AsyncSession, message_id: int, provider: int, stages: dict[Stage, int], slow: bool
) -> None:
    """One message whose stages happened `stages[stage]` ms after T0 (attempt 1 except received)."""
    event = await add_message(db, message_id=message_id, provider_id=provider)
    await add_inbox(db, event, status=InboxStatus.DONE)
    for stage, offset in stages.items():
        db.add(
            StageTiming(
                message_id=message_id,
                event_type="create",
                event_seq=0,
                attempt=0 if stage == Stage.RECEIVED else 1,
                stage=stage,
                at=T0 + ms(offset),
            )
        )
    db.add(
        ModelCall(
            message_id=message_id,
            event_type="create",
            event_seq=0,
            provider="anthropic" if slow else "typesafe",
            model="m",
            role="extract" if slow else "interpret",
            attempt=1,
            request={},
            outcome="ok",
            latency_ms=stages[Stage.PUSHED],
            cost_usd=Decimal("0.01"),
            started_at=T0,
        )
    )
    await db.commit()


async def seed_timings(db: AsyncSession) -> None:
    for n in range(1, 6):  # fast path: queue wait 10..50 ms, jev 100 ms, pushed 10 ms later
        await add_timed_message(
            db,
            1000 + n,
            provider=7,
            stages={
                Stage.RECEIVED: 0,
                Stage.DEQUEUED: 10 * n,
                Stage.JEV: 10 * n + 100,
                Stage.PUSHED: 10 * n + 110,
            },
            slow=False,
        )
    for n, wait in enumerate((20, 40), start=1):  # slow path: LLM 1 s and 3 s
        await add_timed_message(
            db,
            2000 + n,
            provider=8,
            stages={
                Stage.RECEIVED: 0,
                Stage.DEQUEUED: wait,
                Stage.LLM: wait + (1000 if n == 1 else 3000),
                Stage.PUSHED: wait + (1010 if n == 1 else 3010),
            },
            slow=True,
        )


async def test_stage_percentiles_split_fast_and_slow(db_session: AsyncSession) -> None:
    await seed_timings(db_session)
    latency = {
        (r.stage, r.path): r for r in await queries.stage_latency(db_session, T0 - timedelta(1))
    }
    dequeued = latency["dequeued", "fast"]  # 10, 20, 30, 40, 50
    assert (dequeued.count, dequeued.p50_ms, dequeued.p95_ms, dequeued.p99_ms) == (5, 30, 48, 49.6)
    assert latency["jev", "fast"].p50_ms == 100
    llm = latency["llm", "slow"]  # 1000, 3000
    assert (llm.count, llm.p50_ms, llm.p95_ms) == (2, 2000, 2900)
    assert ("jev", "slow") not in latency and ("llm", "fast") not in latency
    total = latency["total", "fast"]  # received -> pushed: 120..160
    assert (total.count, total.p50_ms) == (5, 140)
    assert latency["total", "slow"].p50_ms == 2040  # 1030 and 3050

    waits = {w.provider_id: w for w in await queries.queue_wait(db_session, T0 - timedelta(1))}
    assert (waits[7].count, waits[7].p95_ms, waits[7].max_ms) == (5, 48, 50)
    assert (waits[8].p95_ms, waits[8].max_ms) == (39, 40)
    assert await queries.queue_wait(db_session, NOW) == []  # outside the window


async def test_details_has_every_section(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    await seed_timings(db_session)
    health = HealthService(
        factory,
        clock=FixedClock(NOW),
        config=SimpleNamespace(get=lambda: RuntimeConfig(llm_monthly_budget_usd=100)),  # type: ignore[arg-type]
    )
    d = await health.details("7d")
    assert d.window == "7d" and d.status == "ok"
    assert {(m.provider, m.calls) for m in d.llm.models} == {("anthropic", 2), ("typesafe", 5)}
    assert d.llm.slow_path_share == round(2 / 7, 4)
    assert d.llm.spend_month_to_date_usd == Decimal("0.07")
    assert d.llm.monthly_budget_usd == 100
    assert d.messages.total == 7 and d.messages.failed == 0
    assert d.interpretation is not None and d.interpretation.links.follow_ups == 0
    assert d.postgres.connections >= 1 and d.postgres.lock_waits == 0
    assert d.reconciliation.last_run_at is None and d.orders.by_status == {}
    assert (await health.details("session")).latency == []  # nothing happened since start


async def test_spend_alerts_fire_once_per_level_and_month(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    event = await add_message(db_session)
    clock = FixedClock(datetime(2026, 9, 28, 12, tzinfo=UTC))
    config: Any = SimpleNamespace(get=lambda: RuntimeConfig(llm_monthly_budget_usd=100))

    async def spend(cost: str, at: datetime) -> None:
        db_session.add(
            ModelCall(
                message_id=event.message_id,
                event_type="create",
                event_seq=0,
                provider="openai",
                model="m",
                role="extract",
                attempt=1,
                request={},
                outcome="ok",
                latency_ms=1,
                cost_usd=Decimal(cost),
                started_at=at,
            )
        )
        await db_session.commit()

    async def run_twice() -> list[tuple[str, str | None]]:
        for _ in range(2):
            await check_spend(factory, config=config, alerts=AlertService(factory), clock=clock)
        rows = await db_session.scalars(
            select(Alert).where(Alert.kind == AlertKind.LLM_SPEND).order_by(Alert.created_at)  # type: ignore[arg-type]
        )
        return [(a.severity, a.dedupe_key) for a in rows]

    await spend("500", datetime(2026, 8, 31, 23, tzinfo=UTC))  # last month: not counted
    await spend("79", datetime(2026, 9, 2, tzinfo=UTC))
    assert await run_twice() == []
    await spend("1", datetime(2026, 9, 20, tzinfo=UTC))  # 80 %
    assert await run_twice() == [(Severity.WARNING, "llm_spend:2026-09:warning")]
    await spend("21", datetime(2026, 9, 27, tzinfo=UTC))  # 101 %
    assert [s for s, _ in await run_twice()] == [Severity.WARNING, Severity.URGENT]

    clock.at = datetime(2026, 10, 1, tzinfo=UTC)  # a new month starts from zero
    assert len(await run_twice()) == 2
    unset: Any = SimpleNamespace(get=lambda: RuntimeConfig())
    await check_spend(factory, config=unset, alerts=AlertService(factory), clock=clock)  # no-op


async def add_checkpoints(db: AsyncSession, thread_id: str) -> None:
    params = {"t": thread_id}
    for sql in (
        "INSERT INTO langgraph.checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint,"
        " metadata) VALUES (:t, '', 'c1', CAST('{}' AS jsonb), CAST('{}' AS jsonb))",
        "INSERT INTO langgraph.checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type)"
        " VALUES (:t, '', 'ch', '1', 'x')",
        "INSERT INTO langgraph.checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id,"
        " task_id, idx, channel, type, blob, task_path)"
        " VALUES (:t, '', 'c1', 'task', 0, 'ch', 'x', '\\x00', '')",
    ):
        await db.execute(text(sql), params)
    await db.commit()


async def threads(db: AsyncSession) -> set[str]:
    return set(
        await db.scalars(text("SELECT thread_id FROM langgraph.checkpoints"))  # type: ignore[arg-type]
    )


async def leftovers(db: AsyncSession, table: str) -> int:
    return int(await db.scalar(text(f"SELECT count(*) FROM langgraph.{table}")) or 0)


async def test_prune_deletes_only_old_finished_threads(
    factory: async_sessionmaker[AsyncSession], db_session: AsyncSession
) -> None:
    old, recent = NOW - timedelta(days=40), NOW - timedelta(days=5)
    kept = {"msg:3:create:0:1", "msg:4:create:0:1"}
    for message_id, status, finished in (
        (1, InboxStatus.DONE, old),
        (2, InboxStatus.FAILED, old),
        (3, InboxStatus.DONE, recent),
        (4, InboxStatus.PROCESSING, None),
    ):
        event = await add_message(db_session, message_id=message_id)
        await add_inbox(db_session, event, status=status, finished_at=finished)
    await add_inbox(
        db_session,
        await add_message(db_session, message_id=6, event_type="edit", event_seq=1),
        status=InboxStatus.DONE,
        finished_at=old,
    )
    doomed = {"msg:1:create:0:1", "msg:1:create:0:2", "msg:2:create:0:1", "msg:6:edit:1:1"}
    doomed.add("recheck:1:" + str(uuid.uuid4()))
    kept.add("recheck:3:" + str(uuid.uuid4()))

    signal = await add_signal(db_session, message_id=100)
    cards: dict[str, str] = {}
    for label, state, event_at in (
        ("old_rejected", "rejected", old),
        ("old_pending", "pending", old),
        ("new_rejected", "rejected", recent),
    ):
        card_id = uuid.uuid4()
        cards[label] = f"card:{card_id}"
        await db_session.execute(
            text(
                "INSERT INTO cards (card_id, dedupe_key, source, signal_id, action, card_type,"
                " state, provider_id, proposal, posted_at) VALUES (:id, :key, 'signal', :sig,"
                " :action, 'normal', :state, 7, CAST('{}' AS jsonb), :at)"
            ),
            {
                "id": card_id,
                "key": str(card_id),
                "sig": signal.signal_id,
                "action": ["entry", "add", "trim", "exit", "close"][len(cards) - 1],
                "state": state,
                "at": event_at,
            },
        )
        await db_session.execute(
            text(
                "INSERT INTO card_events (card_id, to_state, reason_code, actor, at)"
                " VALUES (:id, :state, 'rejected', 'user', :at)"
            ),
            {"id": card_id, "state": state, "at": event_at},
        )
    doomed.add(cards["old_rejected"])
    kept |= {cards["old_pending"], cards["new_rejected"]}
    await db_session.commit()

    for thread in doomed | kept:
        await add_checkpoints(db_session, thread)

    removed = await prune_checkpoints(factory, FixedClock(NOW))
    assert removed == len(doomed)
    assert await threads(db_session) == kept
    for table in ("checkpoint_blobs", "checkpoint_writes"):
        assert await leftovers(db_session, table) == len(kept)
    assert await prune_checkpoints(factory, FixedClock(NOW)) == 0


async def test_counters_duplicates_and_semaphore_waits(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(2):
        async with in_transaction(factory, None) as db:
            await inbox.accept(db, to_envelope(ac18()["A1"], NOW))
    assert counts["duplicate"] == 1

    limiter = ProviderLimiter(lambda _: 1)

    async def hold(delay: float) -> None:
        async with limiter.slot("openai"):
            await asyncio.sleep(delay)

    await asyncio.gather(hold(0.05), hold(0))
    assert per_provider("slot") == {"openai": 2}
    assert per_provider("slot_waited") == {"openai": 1}


async def test_interpretation_and_link_stats(db_session: AsyncSession) -> None:
    """F081: path shares from inbox.interpret_outcome / interpret_reasons (F078) and how
    follow-up links were decided (F077), including a code link a person later moved."""
    from app.interpret.models import MessageLink
    from app.ledger.models import ProviderPosition

    traces = [
        ("fast_success", {"route": "fast"}),
        ("jev_success", {"jev": "success", "route": "assemble"}),
        ("jev_success", {"jev": "success", "route": "feed_only"}),
        ("fallback_success", {"jev": "model_failure", "jev_detail": "timeout", "laya": "success"}),
        ("review", {"jev": "ambiguous", "llm": "not_configured", "reasons": ["feed_guard: x"]}),
        (
            "review",
            {"jev": "invalid", "jev_detail": "invalid", "laya": "off", "llm": "not_configured"},
        ),
    ]
    for n, (outcome, trace) in enumerate(traces):
        event = await add_message(db_session, message_id=3000 + n, provider_id=7)
        await add_inbox(
            db_session,
            event,
            status=InboxStatus.DONE,
            interpret_outcome=outcome,
            interpret_reasons=trace,
        )
    spy, qqq = (
        ProviderPosition(
            provider_id=7, underlying=u, status="open", opened_at=T0, instrument="stock"
        )
        for u in ("SPY", "QQQ")
    )
    db_session.add_all([spy, qqq])
    await db_session.flush()
    sigs = [await add_signal(db_session, provider_id=7, message_id=4000 + n) for n in range(4)]

    def link(i: int, outcome: str, rule: str, position: object = None, **kw: Any) -> MessageLink:
        return MessageLink(
            signal_id=sigs[i].signal_id,
            provider_id=7,
            outcome=outcome,
            method=kw.pop("method", "code"),
            provider_position_id=position,
            evidence={"resolver": {}, "rule": rule},
            created_at=T0,
            **kw,
        )

    wrong = link(0, "linked", "code", spy.provider_position_id)
    db_session.add_all(
        [
            wrong,
            link(1, "linked", "reply", qqq.provider_position_id, method="reply"),
            link(2, "ambiguous", "model_proposed", method="jev"),
            link(3, "ambiguous", "unresolved", method="jev"),
        ]
    )
    await db_session.flush()
    db_session.add(  # the user moves signal 0 from SPY to QQQ
        MessageLink(
            signal_id=sigs[0].signal_id,
            provider_id=7,
            outcome="linked",
            method="user_correction",
            provider_position_id=qqq.provider_position_id,
            supersedes_link_id=wrong.id,
            evidence={"resolver": {}, "rule": "user_choice"},
            created_at=T0,
        )
    )
    await db_session.commit()

    stats = await queries.interpretation_stats(
        db_session, T0 - timedelta(days=1), {"jev:requests": 5, "slot:typesafe": 9}
    )
    assert stats.events == 6
    assert stats.outcomes == {
        "fallback_success": 1,
        "fast_success": 1,
        "jev_success": 2,
        "review": 2,
    }
    assert stats.path_rates == {
        "fast": round(1 / 6, 4),
        "jev": round(5 / 6, 4),
        "laya": round(1 / 6, 4),
        "llm": 0.0,
        "review": round(2 / 6, 4),
        "feed_only": round(1 / 6, 4),
    }
    assert stats.jev == {"success": 2, "model_failure": 1, "ambiguous": 1, "invalid": 1}
    assert stats.jev_failures == {"timeout": 1, "invalid": 1}
    assert stats.feed_guard_blocks == 1
    assert stats.counters_since_start == {"jev:requests": 5}
    links = stats.links
    assert (links.follow_ups, links.unique, links.ambiguous, links.model_proposed) == (4, 2, 2, 1)
    assert (links.corrected_by_user, links.wrong_link_rate) == (1, 0.5)
