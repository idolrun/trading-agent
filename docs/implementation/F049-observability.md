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

## Scope — metrics (window selectable: `?window=session|day|7d`, default `day`)
`session` = since process start; `*_since_start` counters ignore the window and reset on restart.
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
| Postgres connections, lock waits | `count(*)` of `pg_stat_activity` (this database) and of `pg_locks` where `NOT granted` — readable by every role, so no `pg_monitor` grant and no migration for it |
- `/health`: 200 `{status:"ok"}` if heartbeat age ≤ 15 s and DB reachable; else 503 (external uptime monitor pages the phone — F064).
- `/health` skips the heartbeat check when `settings.background_enabled` is false (API-only apps and tests); a 503 body is `{status:"unhealthy", reason}`.
- Spend: daily job compares month-to-date `model_calls.cost_usd` with `llm_monthly_budget_usd` → warning at 80%, urgent at 100% (no automatic shutdown — not specified). Each level alerts once per calendar month (UTC): `alerts.dedupe_key = llm_spend:{YYYY-MM}:{severity}`; the highest level crossed wins. New alert kind `llm_spend` (migration `0014`). Skipped while `llm_monthly_budget_usd` is unset.
- Prune: daily job deletes LangGraph checkpoints for threads finished > 30 days (message threads; approval threads whose card is terminal).
- Logs: structured JSON with `message_id`, `provider_id`, `signal_id`, `card_id`, `client_order_id` bound via contextvars (F001).
- No external metrics stack now (YAGNI); SQL + dashboard is the TRD §11 design.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/observability/queries.py` | SQL metric queries | writes |
| `backend/app/observability/counters.py` | in-process counters | — |
| `backend/app/observability/service.py` | `HealthService`, spend + prune jobs | — |
| `backend/migrations/versions/0014_alert_kind_llm_spend.py` | `alerts.kind` gains `llm_spend` | — |
| `backend/tests/integration/observability/test_metrics.py`, `backend/tests/integration/api/test_health_api.py`, `backend/tests/api/test_health.py` (rewritten) | tests | — |

## Files to modify
`app/api/routes/health.py` (`/health` moved here from `main.py`), `app/api/schemas/health.py`, `app/main.py` (`HealthService`, jobs), `app/alerts/models.py` (kind `llm_spend`), `app/inbox/repo.py` (duplicate counter), `app/llm/limiter.py` + `app/llm/chain.py` (slot/429 counters, `breakers_open()`), `tests/integration/api/test_read_api.py`. Deleted: `app/system/queries.py` (`health_details` folded into `HealthService.details`). No `pg_monitor` migration (see the metric table).

## Tests
- Synthetic `stage_timings` → correct percentiles.
- Heartbeat 20 s old → `/health` 503; fresh → 200; background disabled → not required.
- Spend thresholds raise alerts once (job run twice); prune removes only old finished threads.

## Acceptance criteria
Every PRD §16 bullet has a metric in `/api/v1/health/details`.

## Implementation steps
1. Queries. 2. Counters. 3. Service + endpoints. 4. Jobs. 5. Tests.
