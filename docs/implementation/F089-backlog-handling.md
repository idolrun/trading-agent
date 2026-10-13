# F089 — Backlog handling and late unreadable messages

## Objective
Implements the rest of ranked fix #5 from `docs/reviews/signal-bottleneck-2026-10-12.md` §6.2. The backfill ordering part shipped in 442af82, and the first-run lookback for a new channel shipped in 840c4bb. This feature also fixes a regression that 840c4bb introduced.

## Decisions (user, 2026-10-13)
- **Stale skip, narrowed after review (2026-10-13).** The user chose "skip only the slow path". As first built, every stale message Jev couldn't settle skipped Laya and the LLM. Load test 4 and a review of catch-up showed two harms:
  - a late exit Jev was unsure about became a raw review card instead of a readable close card (C90);
  - unsure stale chatter became late review cards instead of feed-only.

  The skip now applies only when Jev's hint reads the message as an opening (`entry` / `add` / `stock_entry`). A stale entry never gets a card, so it ends as a stale `unparsed` signal with reason `stale_skip_slow: …` and no LLM call. A Jev failure, which gives no reading, still goes to the slow path. The fast path and Jev always run, so a stale entry that Jev settles still reaches the provider ledger (FR-1c).
- **Unreadable stale messages are carded again, badged `late`** (C90: a late exit is never silent). There are two exceptions, both shown in the feed as stale with no card:
  - backfilled history;
  - a message Jev's hint read as `entry` / `add` / `stock_entry`, because a stale entry never gets a card.

  The 840c4bb change had silenced every stale unreadable message, including live exits.
- **One late re-link per lane.** Backfill makes every event late, and the re-link re-check used to run the full graph for every later follow-up after each one, under the lane slot. Now:
  - a backfilled late event only records `lane → earliest post time`;
  - the dispatcher's new `on_lane_idle` hook runs the re-check once, under the lane slot, when the lane has nothing waiting;
  - live late events still re-check right away.
- **Crash safety.** On startup, lanes whose late backfilled events finished in the last 8 h are deferred again and flushed. The re-check is idempotent: it only re-reads unlinked or ambiguous follow-ups, and proposal cards are deduplicated.
- **The Discord listener starts before the startup backfill.** The backfill used to wait 10 s per channel for a gateway that wasn't connected yet, then fail. Messages accepted early only queue; the dispatcher still starts after reconciliation.
- **Index.** `ix_inbox_next_for_lane` now includes `backfilled`, matching `next_for_lane`'s `ORDER BY` (migration 0031).
- **Not done.** Catch-up events (live, past 30 s) get no new ordering. The stale skip already removes their slow-path cost.

## Where
| Area | Change |
|---|---|
| `graph/message_graph.py` | `_stale_skip` in `jev_interpret`; `OPENING_HINTS`; `raw_review` passes `opening`; `notify` defers backfilled re-links |
| `pipeline/apply.py` | `apply_raw_review(opening=)`: a stale card unless backfilled or opening; `late` label |
| `ledger/relink.py` | `defer`, `flush`, `restore_pending` |
| `dispatcher/service.py` | `on_lane_idle` hook; `idle(lane)` under the slot |
| `main.py` | idle hook → `relink.flush` + `approvals.start_many`; listener before `startup`; `relink-recovery` task |
| `inbox/models.py`, `migrations/versions/0031_inbox_lane_backfilled.py` | index |

## Tests
- `tests/load/test_outage.py`: test 4 now accepts the `close` card that F085 made of a full exit. It had failed since F085 on an `exit`-only assertion.
- `tests/integration/pipeline/test_apply.py`: a stale live unreadable message gives a late card; a stale backfilled one gives no card and status stale.
- `tests/integration/graph/test_message_graph.py`:
  - stale + unsure Jev reading a follow-up: still goes to the LLM;
  - stale + Jev reading an entry: no LLM call, no card;
  - stale + Jev down: still goes to the LLM.
- `tests/integration/graph/test_out_of_order.py`: a backfilled late event defers, and `flush` proposes once; a restart restores the deferral.
- `tests/integration/dispatcher/test_sequencing.py`: the idle hook runs under the lane slot after the lane drains.
- `tests/unit/llm/test_limiter.py` now restores the process-wide counters. It had leaked a `typesafe` slot counter into the health API test.

## Not covered
- No lifespan-ordering test for listener-before-startup.
- How fast a backfill flood now drains is unmeasured; it needs a replay of a flood like the one on 2026-10-12.
