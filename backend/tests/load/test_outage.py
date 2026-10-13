"""TRD §12 fault test 4 (F063, AC-21): a 10-minute Message API outage, then backlog delivery.

Live: every provider opens a position; its entry card is approved and filled (a user lot, so
a later exit is carded). Outage: the webhook delivers nothing and the history API is down; the
providers post a new entry, an exit of the live position and chatter; the app clock jumps
10 minutes (the pipeline's `RealtimeClock` is advanced: no real 10-minute wait). Recovery: the
webhook comes back with late redeliveries of half the backlog, the feed-health tick sees the feed
again and its recovery hook runs the gap detector (backfills the other half), then the webhook
redelivers the whole backlog once more (duplicates). A fresh entry per provider follows.

Pass (TRD §12): nothing lost, nothing duplicated; CATCH_UP set during the backlog and cleared
after it drained; stale entries have no card (inbox `stale`); exits are carded with the `late`
badge. Drain time is reported.
"""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.cards.edits import Edits
from app.cards.models import Card, CardAction
from app.db import in_transaction
from app.inbox import repo as inbox
from app.inbox.models import InboxItem, MessageEvent
from app.ingest.adapter import to_envelope
from app.ingest.feed_health import FeedHealthMonitor
from app.ingest.gaps import GapDetector
from app.interpret.models import Signal
from app.ledger.models import Lot
from app.messageapi.fake import FakeMessageHistory
from app.system.models import FlagName, SystemFlagEvent
from tests.integration.pipeline.harness import T0
from tests.integration.schema.helpers import add_session
from tests.load import scenarios as sc
from tests.load import synthetic_day as sd
from tests.load.driver import LoadRun, RealtimeClock, Sent, open_run
from tests.load.report import Check, all_ok, info, render_section, upsert

pytestmark = [pytest.mark.load, pytest.mark.integration]

P = sd.PROVIDER_BASE + 600
# asks below the $1000 default tier (one contract affordable); META (18.34) is left out
AFFORDABLE = [c for c in sc.CONTRACTS if float(c[3]) * 100 <= 1000]


@dataclass
class Plan:
    live: list[sd.DayMessage]
    outage: list[tuple[float, sd.DayMessage]]  # (seconds into the outage, message)
    fresh: list[sd.DayMessage]


def plan() -> Plan:
    """Per provider: a live entry; during the outage a new entry (min 1-3), an exit of the live
    position (min 4-6) and chatter (min 7-9); after recovery a fresh entry."""
    live, outage, fresh = [], [], []
    n = len(AFFORDABLE)
    for i in range(sc.OUTAGE_PROVIDERS):
        p = P + i
        mine, other, third = AFFORDABLE[i % n], AFFORDABLE[(i + 3) % n], AFFORDABLE[(i + 6) % n]
        live.append(sd.story_message("entry", p, mine, seed=i))
        minute = 60.0
        outage += [
            (minute * (1 + i % 3) + i, sd.story_message("entry", p, other, seed=100 + i)),
            (minute * (4 + i % 3) + i, sd.story_message("exit", p, mine, seed=200 + i)),
            (minute * (7 + i % 3) + i, sd.story_message("chatter", p, mine, seed=300 + i)),
        ]
        fresh.append(sd.story_message("entry", p, third, seed=400 + i))
    outage.sort(key=lambda t: t[0])
    return Plan(live, outage, fresh)


async def webhook(run: LoadRun, s: Sent) -> bool:
    """A (re)delivery through `inbox.accept`, received now. True = new, False = duplicate."""
    env = to_envelope(s.payload, run.h.clock.now())
    async with in_transaction(run.factory, None) as db:
        return await inbox.accept(db, env)


async def catch_up_events(run: LoadRun) -> list[bool]:
    async with run.factory() as db:
        rows = await db.scalars(
            select(col(SystemFlagEvent.active))
            .where(col(SystemFlagEvent.flag) == FlagName.CATCH_UP)
            .order_by(col(SystemFlagEvent.id))
        )
    return list(rows.all())


async def test_4_message_api_outage_then_backlog(tmp_path: Path, db_session: AsyncSession) -> None:
    p = plan()
    everything = [*p.live, *(m for _, m in p.outage), *p.fresh]
    clock = RealtimeClock(T0 + timedelta(seconds=5))
    history = FakeMessageHistory()
    session_id = await add_session(db_session)
    async with open_run(tmp_path, sd.by_text(everything), clock=clock, warm_up=False) as run:
        gaps = GapDetector(
            run.factory, history=history, config=run.h.config, alerts=run.h.alerts, clock=clock
        )
        monitor = FeedHealthMonitor(
            run.factory, config=run.h.config, flags=run.flags, alerts=run.h.alerts, clock=clock
        )

        async def backfill_on_recovery() -> None:  # the app's wiring (app/main.py)
            await gaps.run_once()

        monitor.on_recovered.append(backfill_on_recovery)
        ids: dict[int, int] = {}

        # --- live: positions opened, approved, filled ---
        live = [run.envelope(m, ids) for m in p.live]
        for s in live:
            history.add(s.payload)
            await run.accept(s)
            monitor.note_message(clock.now())
        await run.wait_terminal([s.message_id for s in live], 30)
        async with run.factory() as db:
            entry_cards = list(
                (await db.scalars(select(Card).where(col(Card.action) == CardAction.ENTRY))).all()
            )
        for card in entry_cards:
            await run.approvals.decide(
                card.card_id,
                "approve",
                card.version,
                Edits(qty=2),
                session_id=session_id,
            )
        await run.consume()
        async with run.factory() as db:
            lots = int(await db.scalar(select(func.count()).select_from(Lot)) or 0)

        # --- outage: webhook silent, history down, 10 minutes pass ---
        history.unavailable = True
        start = clock.now()
        backlog = [run.envelope(m, ids, at=start + timedelta(seconds=t)) for t, m in p.outage]
        for s in backlog:
            history.add(s.payload)  # posted in the channel; nobody tells us
        down_backfill = await gaps.run_once()  # history unavailable: warns, retries later
        clock.advance(timedelta(seconds=sc.OUTAGE_S + 5))
        await monitor.tick()
        degraded = await run.flags.is_active(FlagName.FEED_DEGRADED)

        # --- recovery ---
        t_recovery = time.perf_counter()
        history.unavailable = False
        late_half = backlog[::2]
        accepted_webhook = [await webhook(run, s) for s in late_half]
        fresh = [run.envelope(m, ids) for m in p.fresh]
        for s in fresh:
            history.add(s.payload)
            accepted_webhook.append(await webhook(run, s))
        monitor.note_message(clock.now())
        await monitor.tick()  # feed back -> recovery hook -> gap backfill
        states: list[tuple[float, bool]] = []

        async def ticker() -> None:
            while True:
                await monitor.tick()
                active = await run.flags.is_active(FlagName.CATCH_UP)
                states.append((time.perf_counter() - t_recovery, active))
                await asyncio.sleep(0.2)

        ticks = asyncio.create_task(ticker())
        # the sender retries everything it had queued: all duplicates now
        redelivered = [await webhook(run, s) for s in [*backlog, *live]]
        all_sent = [*live, *backlog, *fresh]
        all_ids = [s.message_id for s in all_sent]
        statuses = await run.wait_terminal(all_ids, 120)
        drained_s = time.perf_counter() - t_recovery
        deadline = time.perf_counter() + 10
        while time.perf_counter() < deadline and (not states or states[-1][1]):  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        ticks.cancel()
        await asyncio.gather(ticks, return_exceptions=True)
        last_on = max((i for i, (_, a) in enumerate(states) if a), default=-1)
        cleared_at = next((t for t, a in states[last_on + 1 :] if not a), None)
        events = await catch_up_events(run)
        set_during = True in events

        # --- effects ---
        async with run.factory() as db:
            stored = Counter(
                (
                    await db.scalars(
                        select(col(MessageEvent.message_id)).where(
                            col(MessageEvent.message_id).in_(all_ids)
                        )
                    )
                ).all()
            )
            queued = Counter(
                (
                    await db.scalars(
                        select(col(InboxItem.message_id)).where(
                            col(InboxItem.message_id).in_(all_ids)
                        )
                    )
                ).all()
            )
            backfilled = set(
                (
                    await db.scalars(
                        select(col(MessageEvent.message_id)).where(
                            col(MessageEvent.message_id).in_(all_ids),
                            col(MessageEvent.backfilled),
                        )
                    )
                ).all()
            )
            sig_rows = (
                await db.execute(select(col(Signal.signal_id), col(Signal.message_id)))
            ).all()
            cards = list((await db.scalars(select(Card))).all())
    msg_of = {sid: mid for sid, mid in sig_rows}
    cards_of: dict[int, list[Card]] = {}
    for c in cards:
        if c.signal_id is not None and c.signal_id in msg_of:
            cards_of.setdefault(msg_of[c.signal_id], []).append(c)

    kind = {s.message_id: s.item.kind for s in all_sent}
    outage_ids = [s.message_id for s in backlog]
    lost = [m for m in all_ids if stored[m] == 0 or queued[m] == 0]
    duplicated = [m for m in all_ids if stored[m] > 1 or queued[m] > 1]
    dup_cards = sum(n - 1 for n in Counter((c.signal_id, c.action) for c in cards).values())
    unprocessed = {m: s for m, s in statuses.items() if s not in ("done", "stale")}
    stale_entries = [m for m in outage_ids if kind[m] == "entry"]
    stale_carded = [m for m in stale_entries if cards_of.get(m) or statuses.get(m) != "stale"]
    exits = [m for m in outage_ids if kind[m] == "exit"]
    exits_not_late = [
        m
        for m in exits
        # C105 (F085): a full exit is a `close` card ("Close entire position")
        if [c.action for c in cards_of.get(m, [])] not in ([CardAction.EXIT], [CardAction.CLOSE])
        or "late" not in (cards_of[m][0].proposal.get("labels") or [])
    ]
    fresh_wrong = [
        s.message_id
        for s in fresh
        if statuses.get(s.message_id) != "done" or len(cards_of.get(s.message_id, [])) != 1
    ]
    expected_backfill = {s.message_id for s in backlog[1::2]}
    duplicates_rejected = sum(not a for a in accepted_webhook + redelivered)
    checks = [
        info(
            "messages: live / outage backlog / fresh after recovery",
            f"{len(live)} / {len(backlog)} / {len(fresh)}",
        ),
        info("user lots opened in the live phase", str(lots)),
        info("feed_degraded during the outage (AC-8)", str(degraded)),
        info("backfilled while history was down", str(down_backfill)),
        Check("lost (not stored or not queued)", str(len(lost)), "== 0", not lost),
        Check("duplicated stored events", str(len(duplicated)), "== 0", not duplicated),
        Check("duplicate cards", str(dup_cards), "== 0", dup_cards == 0),
        info("duplicate deliveries rejected by accept()", str(duplicates_rejected)),
        Check(
            "backfilled by the gap detector = the half the webhook never redelivered",
            f"{len(backfilled)} (expected {len(expected_backfill)})",
            "equal sets",
            backfilled == expected_backfill,
        ),
        Check("not processed (or failed)", str(unprocessed), "== {}", not unprocessed),
        Check("CATCH_UP set during the backlog", str(set_during), "== True", set_during),
        Check(
            "CATCH_UP cleared after the drain",
            f"flag events {events}",
            "ends False",
            bool(events) and events[-1] is False,
        ),
        Check(
            "stale entries carded or not `stale`",
            f"{len(stale_carded)} of {len(stale_entries)}",
            "== 0",
            not stale_carded and bool(stale_entries),
        ),
        Check(
            "exits without exactly one `late` exit card",
            f"{len(exits_not_late)} of {len(exits)}",
            "== 0",
            not exits_not_late and bool(exits),
        ),
        Check("fresh entries not carded normally", str(len(fresh_wrong)), "== 0", not fresh_wrong),
        info("drain time: recovery -> every event processed", f"{drained_s:.2f} s"),
        info(
            "drain time: recovery -> CATCH_UP cleared (no old event left waiting)",
            "never" if cleared_at is None else f"{cleared_at:.2f} s",
        ),
    ]
    upsert(
        "outage",
        render_section(
            f"Test 4: {sc.OUTAGE_S / 60:g}-minute Message API outage, then backlog delivery",
            checks,
            notes=(
                f"{sc.OUTAGE_PROVIDERS} providers. Live: an entry each, approved and filled. "
                "Outage (history API down, webhook silent, app clock advanced "
                f"{sc.OUTAGE_S + 5:g} s): per provider a new entry, an exit of the live "
                "position and chatter. Recovery: late webhook redeliveries of half the backlog "
                "and a fresh entry per provider; the feed-health tick's recovery hook runs the "
                "gap detector (the other half); then the whole backlog and the live messages "
                "are redelivered once more (all duplicates).",
                "Drain time is wall clock from the history API coming back; the model fakes "
                "keep their simulated latency.",
            ),
        ),
    )
    assert all_ok(checks), [c for c in checks if c.ok is False]
