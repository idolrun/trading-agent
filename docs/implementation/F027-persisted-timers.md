# F027 — Persisted Timers

## Objective
Durable deadlines (card expiry, entry-remainder cancel, cutoff 1/2, off-hours release) that survive restarts and fire exactly once.

## Requirements covered
- PRD 7.2 (entry card expiry), C22 (M s remainder cancel), 7.6 (cutoffs 1 and 2), 7.7 (off-hours hold until 9:30 ET), C18, §11 (open cards at crash expire).
- TRD §2.2 `timers` module, §9 startup step 8 (reload timers).

## Dependencies
- Depends on: F005, F007
- Produces: `TimerService.schedule(kind, due_at, refs, dedupe_key)`, `cancel(dedupe_key)`, handler registry `register(kind, handler)`, `TimerScheduler` lifespan task.
- Used by: F026 (card expiry rows), F036 (expiry handler), F039, F041, F045, F046.

## Scope
- Scheduler loop: sleep until `min(due_at)` of scheduled timers (cap 1 s), wake early on `NOTIFY timers`.
- Fire: `UPDATE timers SET status='fired', fired_at=now() WHERE timer_id=:id AND status='scheduled' RETURNING *` (claim = exactly once), then call the handler **after** the claim commits. Handler failure → alert + re-schedule once with +5 s (then alert only).
- Handlers must be idempotent (e.g. expiring an already-approved card is an `IllegalTransition` → ignored).
- Startup: overdue timers fire immediately in `due_at` order (F046 runs this at step 8).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/timers/service.py` | schedule/cancel/registry | kind-specific logic |
| `backend/app/timers/scheduler.py` | loop | — |
| `backend/app/timers/repo.py` | queries | — |
| `backend/tests/integration/timers/test_scheduler.py` | tests (FixedClock + fast loop) | — |

## Tests
- Scheduling twice with the same dedupe key → one row.
- Two schedulers racing (two tasks) → handler runs once.
- Restart with overdue timers → fired on start in order.
- Cancel prevents firing.

## Acceptance criteria
Exactly-once firing under races and restarts.

## Implementation steps
1. Repo. 2. Service + registry. 3. Scheduler loop + NOTIFY. 4. Tests.

## Implementation notes (2026-09-29)
- One `timers/service.py` (`TimerService`: `schedule`, `cancel`, `register`, `run_due`, `run`) instead of a separate `scheduler.py`; queries in `timers/repo.py` (`schedule` — also used by F026 inside the apply tx —, `claim_due`, `next_due`, `cancel`).
- Claim = `UPDATE … SET status='fired' WHERE timer_id = (earliest due … FOR UPDATE SKIP LOCKED) AND status='scheduled' RETURNING`, committed before the handler runs → exactly once across racing schedulers and restarts. Due time comes from the injected `Clock`.
- Only kinds with a registered handler are claimed, so `card_expiry` rows written by F026 wait (and fire as overdue) until F036 registers the expiry handler.
- Handler failure → alert kind **`timer_failed`** (new, migration `0009`, database-schema.md #27) + one reschedule at +5 s with dedupe key `<key>:retry`; a failing retry only alerts.
- Loop wakes on `NOTIFY timers` (sent by `schedule`) or after `min(next due, 1 s)`; started in the lifespan (`app.state.timers`).
