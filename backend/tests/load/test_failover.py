"""TRD §12 fault test 6 (F063, AC-23): Postgres stop/start during a burst.

Local stand-in for a managed failover: `docker compose stop postgres` mid-burst, `start` after
`LOAD_FAILOVER_DOWN_S` (default 10 s; the database is shared, keep it short). The sender acts
like the Message API webhook: an event counts as *acknowledged* only when `inbox.accept`
committed (the route maps a DB error to 503 and the sender retries); unacknowledged events are
retried every 0.5 s.

Two recovery phases, measured separately:
- A. in-process: the same stack keeps running (listeners reconnect, the pool pre-pings). How long
  until the pipeline processes new messages again, and does every acknowledged event finish
  without intervention (bounded wait `IN_PROCESS_WAIT_S`)?
- B. restart through the TRD §9 startup sequence ("after a failover or restore: run the full
  startup sequence"): fresh services on the same DB, broker and clock. Entries are blocked until
  the reconciliation against the broker is clean.

The burst forces the slow path (as test 3) so model calls are in flight when the database
stops: the worst case for the dispatcher, whose in-flight events must fail and be retried.

RTO = from the stop command until every acknowledged event is processed and entries are open
again (phase B end), must be <= 5 min. `conftest.py` orders this test last in the suite; Postgres
is always started again in `finally`.

Phase A also checks the in-process failover recovery (`recover_after_failover`, wired into the
dispatcher as in `create_app`): workers hold on a connection error, the failed event is released
once the database answers (never left `processing`), entries are blocked and reconciled before
processing resumes, so no entry is carded approvable between the database's return and the
end of that recovery.
"""

import asyncio
import shutil
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.cards.edits import Edits
from app.cards.models import Card, CardAction, CardState, CardType
from app.execution.models import Order, OrderKind
from app.inbox.models import InboxItem, MessageEvent
from app.messageapi.fake import FakeMessageHistory
from app.runtime_config.schema import TradingMode
from app.system.models import FlagName, SystemFlagEvent
from tests.integration.conftest import _url
from tests.integration.pipeline.harness import T0
from tests.integration.schema.helpers import add_session
from tests.load import scenarios as sc
from tests.load import synthetic_day as sd
from tests.load.driver import LoadRun, RealtimeClock, Sent, libpq_dsn, open_run
from tests.load.report import Check, info, render_section, upsert

pytestmark = [pytest.mark.load, pytest.mark.integration]

REPO = Path(__file__).parents[3]
BURST_MESSAGES, BURST_WINDOW_S, BURST_PROVIDERS = 50, 10.0, 10
STOP_AT_S = 3.0  # into the burst
IN_PROCESS_WAIT_S = 60.0  # phase A: longer than any retry / reconnect interval in the app
RETRY_S = 0.5
STUCK_CHECK = "A. in-process: acknowledged events not finished without intervention"
APPROVABLE = (CardType.NORMAL, CardType.INFERRED)
P = sd.PROVIDER_BASE + 1000  # clear of the other load tests' providers
ITEMS = [
    replace(m, provider=m.provider - sd.PROVIDER_BASE + P)
    for m in sd.cross_burst(sc.SEED + 6, BURST_MESSAGES, BURST_WINDOW_S, BURST_PROVIDERS)
]
LOT_ENTRY = sd.story_message("entry", P + 90, sc.CONTRACTS[0], seed=9)
RESTART_GAP = [
    sd.story_message("entry", P + 91 + i, sc.CONTRACTS[i], seed=90 + i) for i in range(5)
]


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["docker", "compose", *args],  # noqa: S607 — docker from PATH, as in the README
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def docker_ready() -> str | None:
    """None when the compose service can be stopped and started; else why not."""
    if shutil.which("docker") is None:
        return "docker CLI not found"
    ps = compose("ps", "--status", "running", "--services")
    if ps.returncode != 0:
        return f"docker compose unavailable: {ps.stderr.strip()[:200]}"
    if "postgres" not in ps.stdout.split():
        return "compose service `postgres` is not running"
    return None


async def wait_db(timeout_s: float = 120.0) -> tuple[float, datetime]:
    """Until the test database accepts a query: (perf_counter, the database's now())."""
    dsn = libpq_dsn(_url("test_database_url"))
    deadline = time.perf_counter() + timeout_s
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=2) as conn:
                row = await (await conn.execute("SELECT now()")).fetchone()
            assert row is not None
            return time.perf_counter(), row[0]
        except psycopg.Error:
            if time.perf_counter() > deadline:
                raise
            await asyncio.sleep(0.2)


@dataclass
class Sender:
    """The webhook side: an event is acknowledged only once accept() committed."""

    run: LoadRun
    attempts: int = 0
    refused: int = 0  # 503s: the DB error surfaced to the sender, which will retry
    other_errors: int = 0

    async def deliver(self, s: Sent) -> None:
        while True:
            self.attempts += 1
            try:
                await self.run.accept(s)
                return
            except (SQLAlchemyError, OSError):
                self.refused += 1
            except Exception:  # anything else is a bug in the path, still retried
                self.other_errors += 1
            await asyncio.sleep(RETRY_S)


@dataclass
class Snapshot:
    statuses: Counter[str]
    stored: set[int]
    queued: set[int]


async def snapshot(run: LoadRun, ids: list[int]) -> Snapshot:
    async with run.factory() as db:
        rows = (
            await db.execute(
                select(col(InboxItem.message_id), col(InboxItem.status)).where(
                    col(InboxItem.message_id).in_(ids)
                )
            )
        ).all()
        stored = set(
            (
                await db.scalars(
                    select(col(MessageEvent.message_id)).where(
                        col(MessageEvent.message_id).in_(ids)
                    )
                )
            ).all()
        )
    return Snapshot(Counter(str(s) for _, s in rows), stored, {int(m) for m, _ in rows})


async def _never() -> bool:
    return False


async def open_lot(run: LoadRun, session_id: uuid.UUID) -> None:
    """A user lot before the burst, so the startup reconciliation compares a real position."""
    s = run.envelope(LOT_ENTRY, {})
    await run.accept(s)
    await run.wait_terminal([s.message_id], 30)
    async with run.factory() as db:
        card = await db.scalar(select(Card).where(col(Card.action) == CardAction.ENTRY))
    assert card is not None and card.state == CardState.PENDING, card
    await run.approvals.decide(
        card.card_id,
        "approve",
        card.version,
        Edits(),
        session_id=session_id,
        consume_step_up=_never,
    )
    await run.consume()


def watch_workers(run: LoadRun) -> list[str]:
    """Diagnostic: exceptions that escape `Dispatcher._process_next` end the provider's worker
    task (the dispatcher only catches processing errors, not its own claim / mark
    transactions); a later NOTIFY or `discover` starts a new worker."""
    deaths: list[str] = []
    real = run.dispatcher._process_next

    async def watched(provider_id: int) -> bool:
        try:
            return await real(provider_id)
        except Exception as exc:
            deaths.append(type(exc).__name__)
            raise

    run.dispatcher._process_next = watched  # type: ignore[method-assign]
    return deaths


async def block_windows(run: LoadRun) -> list[tuple[datetime, datetime | None]]:
    """(set, cleared) DB times of every `entries_blocked` activation."""
    async with run.factory() as db:
        rows = (
            await db.execute(
                select(col(SystemFlagEvent.active), col(SystemFlagEvent.at))
                .where(col(SystemFlagEvent.flag) == FlagName.ENTRIES_BLOCKED)
                .order_by(col(SystemFlagEvent.id))
            )
        ).all()
    windows: list[tuple[datetime, datetime | None]] = []
    for active, at in rows:
        if active and (not windows or windows[-1][1] is not None):
            windows.append((at, None))
        elif not active and windows and windows[-1][1] is None:
            windows[-1] = (windows[-1][0], at)
    return windows


def blocked_at(windows: list[tuple[datetime, datetime | None]], at: datetime) -> bool:
    return any(start <= at and (end is None or at < end) for start, end in windows)


async def entry_cards(run: LoadRun) -> list[tuple[CardType, datetime, str | None]]:
    async with run.factory() as db:
        rows = (
            await db.execute(
                select(col(Card.card_type), col(Card.created_at), col(Card.proposal)).where(
                    col(Card.action) == CardAction.ENTRY
                )
            )
        ).all()
    return [(CardType(t), at, (prop or {}).get("disabled_reason")) for t, at, prop in rows]


def seconds(value: float | None) -> str:
    return "never" if value is None else f"{value:.2f} s"


async def test_6_postgres_failover_during_burst(tmp_path: Path, db_session: AsyncSession) -> None:
    if reason := docker_ready():
        pytest.skip(f"failover test needs docker compose: {reason}")
    plans = sd.by_text([*ITEMS, LOT_ENTRY, *RESTART_GAP])
    clock = RealtimeClock(T0 + timedelta(seconds=5))
    history = FakeMessageHistory()  # messages posted while the process restarts
    session_id = await add_session(db_session)
    common: dict[str, Any] = {"clock": clock, "trading_mode": TradingMode.PAPER}
    t_stop = t_up = 0.0
    db_up_at = stop_at = datetime.now(UTC)
    try:
        async with open_run(tmp_path, plans, force_slow=True, **common) as run:  # see docstring
            broker = run.broker
            await open_lot(run, session_id)
            sender = Sender(run)
            deaths = watch_workers(run)
            deliveries: list[asyncio.Task[None]] = []
            ids: dict[int, int] = {}
            sent: list[Sent] = []
            t0 = time.perf_counter()

            async def outage() -> None:
                nonlocal t_stop, t_up, db_up_at, stop_at
                await asyncio.sleep(STOP_AT_S)
                t_stop, stop_at = time.perf_counter(), datetime.now(UTC)
                out = await asyncio.to_thread(compose, "stop", "postgres")
                assert out.returncode == 0, out.stderr
                await asyncio.sleep(max(sc.FAILOVER_DOWN_S - (time.perf_counter() - t_stop), 0))
                out = await asyncio.to_thread(compose, "start", "postgres")
                assert out.returncode == 0, out.stderr
                t_up, db_up_at = await wait_db()

            fault = asyncio.create_task(outage())
            for item in ITEMS:
                await asyncio.sleep(max(t0 + item.offset_s - time.perf_counter(), 0))
                s = run.envelope(item, ids)
                sent.append(s)
                deliveries.append(asyncio.create_task(sender.deliver(s)))
            await fault
            await asyncio.gather(*deliveries)  # the sender retried until every one was acked
            all_ids = [s.message_id for s in sent]

            # --- phase A: the running process, no intervention ---
            statuses_a = await run.wait_terminal(all_ids, IN_PROCESS_WAIT_S)
            t_a_end = time.perf_counter()
            unfinished_a = {m: st for m, st in statuses_a.items() if st not in ("done", "stale")}
            new_done = [
                run.timing.finished[s.message_id]
                for s in sent
                if s.t_committed > t_up and s.message_id in run.timing.finished
            ]
            first_new_s = min(new_done) - t_stop if new_done else None
            errors_a = Counter(e.split(":")[0] for _, e in run.timing.errors)
            recovery = [w for w in await block_windows(run) if w[0] > stop_at]
            released_at = recovery[0][1] if recovery else None

            def before_release(at: datetime) -> bool:
                return at > stop_at and (released_at is None or at < released_at)

            approvable_a = [
                at for t, at, _ in await entry_cards(run) if t in APPROVABLE and before_release(at)
            ]
            async with run.factory() as db:
                entry_orders_a = [
                    at
                    for at in (
                        await db.scalars(
                            select(col(Order.created_at)).where(col(Order.kind) == OrderKind.ENTRY)
                        )
                    ).all()
                    if before_release(at)
                ]
            unacked_committed = sum(not s.accepted for s in sent)  # committed, ack lost, resent

        # --- phase B: restart through the TRD §9 startup sequence ---
        gap = [run.envelope(m, ids) for m in RESTART_GAP]
        for s in gap:
            history.add(s.payload)  # posted while the process is down: backfilled at step 7
        async with open_run(
            tmp_path, plans, broker=broker, history=history, restart=True, **common
        ) as run:
            every = [*all_ids, *(s.message_id for s in gap)]
            budget = max(sc.RTO_S - (time.perf_counter() - t_stop), 1.0)
            statuses_b = await run.wait_terminal(every, budget)
            t_b_end = time.perf_counter()
            released = not await run.flags.is_active(FlagName.ENTRIES_BLOCKED)
            snap = await snapshot(run, every)
            report = run.startup_report
            windows = await block_windows(run)
            cards = await entry_cards(run)
            async with run.factory() as db:
                card_rows = (
                    await db.execute(
                        select(
                            col(Card.signal_id),
                            col(Card.action),
                            col(Card.source),
                            col(Card.dedupe_key),
                            col(Card.state),
                        )
                    )
                ).all()
    finally:
        if compose("start", "postgres").returncode == 0:  # never leave the shared DB down
            await wait_db()

    lost = [m for m in every if m not in snap.stored or m not in snap.queued]
    unprocessed = {m: st for m, st in statuses_b.items() if st not in ("done", "stale")}
    keys = Counter((r[0], r[1]) for r in card_rows if r[0] is not None)  # unique in the schema
    dup_cards = sum(n - 1 for n in keys.values())
    no_signal = [f"{r[2]}/{r[1]}/{r[4]} {r[3]}" for r in card_rows if r[0] is None]
    approvable_blocked = [at for t, at, _ in cards if t in APPROVABLE and blocked_at(windows, at)]
    gap_disabled = sum(t == CardType.DISABLED and blocked_at(windows, at) for t, at, _ in cards)
    rto_b = t_b_end - t_stop
    down_s = t_up - t_stop
    checks = [
        info(
            "burst / outage",
            f"{len(ITEMS)} messages over {BURST_WINDOW_S:g} s from {BURST_PROVIDERS} providers; "
            f"Postgres stopped at +{STOP_AT_S:g} s for {sc.FAILOVER_DOWN_S:g} s "
            f"(stop -> accepting queries again {down_s:.1f} s)",
        ),
        Check(
            "deliveries refused while the DB was down (503, not acknowledged)",
            str(sender.refused),
            "> 0",
            sender.refused > 0,
        ),
        Check(
            "unexpected sender errors", str(sender.other_errors), "== 0", not sender.other_errors
        ),
        info("delivery attempts (incl. retries)", str(sender.attempts)),
        info("committed but unacknowledged, redelivered as duplicate", str(unacked_committed)),
        Check("acknowledged events lost", str(len(lost)), "== 0", not lost),
        Check("duplicate cards", str(dup_cards), "== 0", dup_cards == 0),
        info("cards without a signal", str(no_signal)),
        info("processing exceptions during the outage (retried)", str(dict(errors_a))),
        Check(
            "dispatcher worker tasks ended by a DB error (claim / mark)",
            f"{len(deaths)} {dict(Counter(deaths))}",
            "== 0",
            not deaths,
        ),
        Check(
            "A. in-process: stop -> pipeline processes new messages again",
            seconds(first_new_s),
            f"<= {sc.RTO_S:g} s",
            first_new_s is not None and first_new_s <= sc.RTO_S,
        ),
        Check(
            STUCK_CHECK,
            f"{len(unfinished_a)} {dict(Counter(unfinished_a.values()))} after "
            f"{t_a_end - t_up:.1f} s",
            "== 0",
            not unfinished_a,
        ),
        Check(
            "A. in-process: failover recovery blocked entries, reconciled, released",
            "never" if not recovery else f"released={released_at is not None}",
            "released=True",
            released_at is not None,
        ),
        Check(
            "A. in-process: entry orders before the failover recovery reconciled and released",
            str(len(entry_orders_a)),
            "== 0",
            not entry_orders_a,
        ),
        info(
            "A. in-process: entry cards carded approvable in that window (in flight at the stop)",
            f"{len(approvable_a)} (approval revalidates `entries_blocked`)",
        ),
        Check(
            "B. startup reconciliation clean",
            str(report and report.clean),
            "== True",
            bool(report and report.clean),
        ),
        Check(
            "B. approvable entry cards created while entries were blocked",
            str(len(approvable_blocked)),
            "== 0",
            not approvable_blocked,
        ),
        info("B. entry cards disabled by the startup block", str(gap_disabled)),
        Check("B. entries open again after startup", str(released), "== True", released),
        Check(
            "B. events not processed after restart (incl. restart-gap backfill)",
            str(unprocessed),
            "== {}",
            not unprocessed,
        ),
        Check(
            "RTO: stop -> all processed, entries open (B)",
            f"{rto_b:.2f} s",
            f"<= {sc.RTO_S:g} s",
            rto_b <= sc.RTO_S and released and not unprocessed,
        ),
    ]
    upsert(
        "failover",
        render_section(
            "Test 6: Postgres stop/start during a burst",
            checks,
            notes=(
                "Local stand-in for a managed failover: `docker compose stop/start postgres`. "
                "The sender is the webhook: a delivery counts as acknowledged only when "
                "`inbox.accept` committed (the route answers 503 on a DB error); refused ones "
                f"are retried every {RETRY_S:g} s.",
                "A (in-process): the same stack keeps running; the LISTEN connections reconnect "
                "and re-run `discover`, the pool pre-pings. First-new-processed is from the stop "
                "command to the first `process()` return of a message committed after the DB "
                "accepted queries again.",
                "B (restart): fresh services on the same DB, broker and clock through "
                "`startup()` (TRD §9: 'after a failover or restore: run the full startup "
                f"sequence'); {len(RESTART_GAP)} entries posted while the process was down are "
                "backfilled at step 7. RTO is measured from the stop to B's end, so it also "
                "contains phase A's wait: an upper bound.",
                "AC-23 in-process: on a connection error the dispatcher holds its workers and "
                "runs `recover_after_failover` once the DB answers (block entries, reconcile, "
                "backfill, due timers, release if clean, resume approvals); a failed event is "
                "released back to `received` with retries, so it is never left `processing`.",
                "Slow path forced for the burst (as test 3) so model calls are in flight when "
                "the DB stops.",
            ),
        ),
    )
    failed = [c for c in checks if c.ok is False]
    assert not failed, failed
