# Load and fault tests (F063, TRD §12)

The in-process pipeline under load against the real PostgreSQL test database: `inbox.accept` ->
Dispatcher (LISTEN `inbox`, one worker per provider) -> message graph (the real Jev client, router,
LLM chain and extract agent) -> card + approval graph (checkpointer) -> NOTIFY -> EventBus ->
PushService (stamps `pushed`). Market data comes from `tests/fixtures/market`, the broker is
`SimulatedBroker`, and the engine is pooled. `create_app` is never called.

Tests 1-3 (latency) are in `test_latency.py`; the fault tests are `test_outage.py` (4),
`test_kill.py` (5) and `test_failover.py` (6).

## Run

```sh
cd backend && set -a; source ../.env; set +a
DATABASE_URL=$TEST_DATABASE_URL uv run pytest tests/load -m load -q
```

Postgres must be up (`docker compose up -d postgres` from the repo root). Without `-m load` every
test here is skipped ("load suite: run with -m load"). The whole suite takes about 6 minutes: the
10x day (2 minutes), the 1x slice (90 s), the 23 kill cases (1 minute) and the failover test
(25 s, or about 90 s when it hits the known dispatcher bug, see test 6).

Test 6 **stops and starts the compose `postgres` service** (`docker compose stop/start postgres`,
run from the repo root), so it needs the docker CLI and a running compose service; otherwise it
is skipped with the reason. The database is down for `LOAD_FAILOVER_DOWN_S` seconds (default 10)
and the test always starts it again in `finally`. `conftest.py` orders it after every other load
test. Anything else using the database at that moment (another test run, a dev server) sees the
outage too: run it alone, or `-k "not failover"` to skip it.

Each test writes its section into `docs/reviews/load-<YYYY-MM-DD>.md` **before** asserting, so a
failed run still leaves its numbers there. A re-run replaces that test's section. The generator,
fakes and report helpers are covered by `tests/unit/load` in the default run.

## What is measured

The driver measures everything with `time.perf_counter()`. It does not use `stage_timings`: the
`received` and `dequeued` stages use the app clock, the later stages use DB `now()`, and the two
disagree whenever the app clock is not the wall clock. The app clock is a `RealtimeClock` that
starts at Monday 2026-09-28 10:30 ET and advances in real time, so entries fall inside regular
hours and are never stale.

- **inbox insert**: the `inbox.accept` transaction, from start to commit (p99 < 50 ms).
- **queue wait**: from the accept commit to the start of the message's first `process()` call,
  per provider (p95 <= 1 s). Test 2 asserts it; tests 1 and 3 report it for information only,
  because a single-provider burst queues by design (TRD §2.3).
- **end to end**: from the start of the accept to the return of `process()`. By then the card is
  committed and the approval thread has started. Pass: p95 <= 5 s, p99 <= 8 s.
- **failed / retried / lost**: poisoned inbox rows, processing exceptions, and messages that were
  not accepted or never finished. Each must be 0.

## Fault tests (4-6)

- **Test 4, Message API outage** (`test_outage.py`, AC-21). 8 providers each open a position live
  (entry card approved and filled, so a later exit is carded). Then the history API is down
  (`FakeMessageHistory.unavailable`) and the webhook silent while each provider posts a new entry,
  an exit of the live position and chatter; the app clock (`RealtimeClock.advance`) jumps 605 s
  instead of waiting. Recovery: late webhook redeliveries of half the backlog plus a fresh entry
  per provider; the feed-health tick sees the feed again and its recovery hook runs the gap
  detector (the other half, tagged `backfilled`), as `app/main.py` wires it; then everything is
  redelivered once more. Pass: every message stored and queued exactly once, no duplicate card,
  CATCH_UP set during the backlog and cleared after it, stale entries without a card (inbox
  `stale`), each exit with one exit card labelled `late`. Drain time is wall clock from the
  history API coming back.
- **Test 5, kill -9 in each graph node** (`test_kill.py`, AC-7). `Killed(BaseException)` (nothing
  in the app catches it) at the entry or the exit of every message-graph node (`load_context`,
  `find_candidates`, `jev_interpret`, `llm_extract`, `decide_apply`, `raw_review`, `notify`) and
  every approval-graph node (`await_approval`, `revalidate`, `execute`, plus inside the broker
  submit after the broker accepted). Message nodes are wrapped on `_Nodes` before the graph is
  compiled, approval nodes through `interrupt()` and the service's `revalidate` / `execute`; no
  app code changes. The stack is then torn down with its work in flight and a fresh one starts on
  the same DB, checkpointer, broker and clock through the real `startup()`. The message scenario
  routes through every node: a fast entry, a slow (LLM) entry, a slow message whose LLM answer
  fails literal-span verification (raw review) and chatter. Pass: every message processed,
  exactly the expected cards per message, no duplicate card, at most one order per card, no
  broker re-submit of a client order id, no broker order unknown to the orders table. A `none`
  case per graph checks the expectations without a kill.
- **Test 6, Postgres failover during a burst** (`test_failover.py`, AC-23). 50 messages over 10 s
  from 10 providers (slow path forced, so model calls are in flight), Postgres stopped at +3 s.
  The sender is the webhook: a delivery counts as acknowledged only when `inbox.accept`
  committed (the route answers 503 on a DB error), refused ones are retried every 0.5 s. Phase A
  watches the running process recover by itself (up to 60 s); phase B restarts it through
  `startup()` (TRD §9: after a failover run the full startup sequence), with 5 entries posted
  while it was down backfilled at step 7.
  - **RTO** is measured from the `docker compose stop` call: (A) until the first message
    committed after the DB accepted queries again has been processed, and (B) until every
    acknowledged event is processed and entries are unblocked after the restart. B includes
    phase A's wait, so it is an upper bound. Both must be <= 5 min.
  - Pass: no acknowledged event lost, no duplicate card, startup reconciliation clean, no
    approvable entry card created while `entries_blocked` was set (flag audit vs card
    `created_at`), entries open again.
  - Phase A also passes only if no dispatcher worker died, every acknowledged event finished
    without a restart, and the in-process failover recovery (`recover_after_failover`: block
    entries, reconcile, release) ran before any entry was carded approvable again.

## The fakes (`fakes.py`)

Only the network call is fake. The real code builds each request and parses each answer.

- `LatencyJev` replaces `AsyncTypeSafeClient.system_one`. It sleeps a sampled Jev latency, then
  answers the questions the real client asked, using the candidate and book ids from the request.
  A confident answer is assembled in code (the fast path). An unsure answer (`msg_type` confidence
  0.55, below `t_field` and `t_chat`) is sent by the real router to the LLM (the slow path).
- `LatencyChat` replaces the chat model behind the extract agent. It sleeps a sampled LLM latency,
  reads the message from the `## Context` JSON that the real middleware put in the system prompt,
  and returns the `LlmParsedSignals` tool call for it. The answer passes the real literal-span
  verification.
- The answers come from `synthetic_day.DayMessage.signal`. `tests/unit/load` checks that they
  route and verify as intended.

## Latency parameters are placeholders

`scenarios.py` fits each latency to a lognormal from a median and a p95, then caps it:

| Model | median | p95 | cap | why the cap |
|---|---|---|---|---|
| Jev | 120 ms | 260 ms | 290 ms | `jev.timeout_ms` is 300. Under load a few samples still time out and fall back to the slow path, which is realistic. |
| LLM extract | 900 ms | 1800 ms | 1950 ms | below `per_attempt_cap_ms` 2000 and `deadline_ms` 3000; above them a call becomes raw review + `parser_degraded`, which is a different scenario |

These numbers are **not measured yet**. Replace them with the F048 bench numbers once those runs
are in `model_calls`:

```sql
SELECT provider, model, role,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
       count(*)
FROM model_calls
WHERE outcome = 'ok' AND started_at > now() - interval '14 days'
GROUP BY provider, model, role;
```

To use the new numbers, either set the env knobs below or edit `JEV_DEFAULT` / `LLM_DEFAULT`.
Before shadow, do one run with real models in staging (F063 scope).

## Scenarios

- **Test 1, day replay** (`synthetic_day.generate`) is seeded and deterministic. It is 400
  messages over 20 minutes from 12 providers with skewed activity: about 60% chatter/commentary,
  then entry stories (a quarter of them as an entry+stop+targets "pack" within seconds) followed
  by adds, trims, stop moves and exits, and about 40% of follow-ups are replies to the entry.
  About 20% of the actionable text (4% of chatter) is unclear to Jev and goes down the slow path.
  The 10x run takes 2 minutes of wall time and the 50x run takes 24 s. The 1x run replays only
  the first 90 s (the busy open). A real day at 1x would take 6-8 hours, and a real day averages
  0.1 msg/s while this one averages 0.33 msg/s. Replay a real Message API export here once one
  exists.
- **Test 2** sends 50 messages in 5 s across 10 providers, 5 per provider, uniform over the
  window. It is mostly actionable (30% feed). Each provider is at the single-provider design
  burst at the same moment.
- **Test 3** sends 5 messages in 3 s from one provider with the slow path forced: Jev answers
  every message (with its latency) as unsure, so every message also goes through the LLM.
  `spread` is evenly over 3 s. `pack` puts all 5 inside the first 0.45 s ("entry + stop + targets
  posted together", PRD §12), which is the worst case. If only test 3 fails, the report names the
  OD-19 candidate (parallel interpret, serial apply, TRD §2.3); **not** Kafka/Redis.

## Env knobs (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `LOAD_DAY_MESSAGES` | 400 | messages in the synthetic day |
| `LOAD_DAY_SPAN_S` | 1200 | day length in seconds (the 10x wall time is span / 10) |
| `LOAD_1X_SLICE_S` | 90 | seconds of the day replayed at 1x |
| `LOAD_SPEEDS` | `1,10,50` | replay speeds |
| `LOAD_SEED` | 20260928 | generator and latency seed |
| `LOAD_SLOW_PCT` | 20 | % of actionable messages Jev is unsure of |
| `LOAD_JEV_MEDIAN_MS`, `LOAD_JEV_P95_MS` | 120, 260 | Jev latency |
| `LOAD_LLM_MEDIAN_MS`, `LOAD_LLM_P95_MS` | 900, 1800 | LLM extract latency |
| `LOAD_LATENCY_SCALE` | 1.0 | multiplies every model latency (caps scale up too) |
| `LOAD_DB_POOL_SIZE` | 50 | SQLAlchemy pool (+10 overflow); the app default `DB_POOL_SIZE` is 10 |
| `LOAD_REPORT_DIR` | `<repo>/docs/reviews` | where `load-<date>.md` is written |
| `LOAD_FAILOVER_DOWN_S` | 10 | test 6: seconds Postgres stays stopped |

The test database `trading_test` is shared with the integration suite, and tables are truncated
after each test. Do not run this suite while an integration run is in progress (test 6 also
stops the whole Postgres container, i.e. the dev database `trading` too).
