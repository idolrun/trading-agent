# F049 — Observability & Health Metrics

## Objective
Make every PRD §16 signal visible: per-stage latency percentiles, queue wait, backlog, counts, LLM stats and spend, card/order outcomes, invariant blocks, reconciliation and feed health — computed as SQL over existing tables, plus `/health` that fails on a stale dispatcher heartbeat.

## Requirements covered
- PRD §16 (all bullets), §12 latency measurement (p95/p99 incl. bursts, per stage), C38, C44 (spend), AC-1 (measured), AC-22 (measured).
- TRD §2.4 watchdog (`/health` fails if heartbeat > 15 s), §5.2 (prune checkpoints after 30 days), §11 metric list.

## Dependencies
- Depends on: F035, F040
- Produces: `HealthService.details()`, `/health` (liveness + heartbeat), extended `GET /api/v1/health/details`, spend alert job, checkpoint prune job, in-process counters (duplicates, 429s, semaphore waits).
- Used by: F058, F063, F064.

## Scope — metrics (window selectable: session, day, 7 d)
| Metric | Source |
|---|---|
| per-stage p50/p95/p99, split fast/slow path | `stage_timings` (+ `model_calls.role` presence of `extract`) — `percentile_cont` |
| queue wait per provider (p95, max) | `stage_timings` received → dequeued by provider |
| backlog depth, oldest unprocessed age, catch-up | `inbox`, `system_flags` |
| duplicates, late, stale, backfilled, failed counts | in-process counter (duplicates), `inbox` flags/status |
| semaphore saturation, 429 rate | in-process counters (F031/F033) |
| LLM per provider/model: latency p50/p95, error rate by outcome, breaker state, slow-path share, spend | `model_calls`, breaker state |
| card outcomes | `cards.state`, `card_events.reason_code` |
| order states, rejects, reprice counts | `orders`, `order_events` |
| invariant blocks | `alerts(kind='invariant_block')` |
| reconciliation results | `alerts(kind='reconcile_mismatch')`, last `ReconcileReport` |
| feed health | F013 state |
| Postgres connections, lock waits | `pg_stat_activity`, `pg_locks` (read-only role grants: `pg_monitor`) |
- `/health`: 200 `{status:"ok"}` if heartbeat age ≤ 15 s and DB reachable; else 503 (external uptime monitor pages the phone — F064).
- Spend: daily job compares month-to-date `model_calls.cost_usd` with `llm_monthly_budget_usd` → warning at 80%, urgent at 100% (no automatic shutdown — not specified).
- Prune: daily job deletes LangGraph checkpoints for threads finished > 30 days (message threads; approval threads whose card is terminal).
- Logs: structured JSON with `message_id`, `provider_id`, `signal_id`, `card_id`, `client_order_id` bound via contextvars (F001).
- No external metrics stack now (YAGNI); SQL + dashboard is the TRD §11 design.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/observability/queries.py` | SQL metric queries | writes |
| `backend/app/observability/counters.py` | in-process counters | — |
| `backend/app/observability/service.py` | `HealthService`, spend + prune jobs | — |
| `backend/tests/integration/observability/test_metrics.py`, `backend/tests/api/test_health.py` (extend) | tests | — |

## Files to modify
`app/api/routes/health.py`, `app/api/schemas/health.py`, `app/main.py` (jobs), migration granting `pg_monitor` to `trading_app` if required (document in schema doc).

## Tests
- Synthetic `stage_timings` → correct percentiles.
- Heartbeat 20 s old → `/health` 503.
- Spend thresholds raise alerts once.

## Acceptance criteria
Every PRD §16 bullet has a metric in `/api/v1/health/details`.

## Implementation steps
1. Queries. 2. Counters. 3. Service + endpoints. 4. Jobs. 5. Tests.
