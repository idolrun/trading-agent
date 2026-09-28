# F008 — Alerts & System Flags

## Objective
A single place to raise alerts and to set/read the global safety flags (entries blocked, kill switch, catch-up, feed degraded, parser degraded) with a full audit trail.

## Requirements covered
- PRD FR-26 (alerts pushed — push itself in F029), FR-4 (degraded state), FR-1c (catch-up mode), FR-1e (poison alert), FR-9 (parser degraded alert), §11 (reconciliation mismatch blocks entry cards), 7.9 (kill switch pauses new cards and entries), §14 (alerts audited).
- TRD §9 startup sequence step 1 "block entry cards".

## Dependencies
- Depends on: F004
- Produces: `AlertService.raise_(…)`, `AlertService.acknowledge(…)`, `FlagService.set(…)`, `FlagService.is_active(…)`, `FlagService.entries_allowed()`; `NOTIFY alerts` / `NOTIFY flags`.
- Used by: F012, F013, F028, F029, F030, F031, F046, F047.

## Scope
- `raise_(kind, severity, message, *, refs, details, dedupe_key=None, session=None)` — inserts `alerts` (`ON CONFLICT (dedupe_key) DO NOTHING`), `NOTIFY alerts, alert_id`. Can join a caller's transaction.
- `acknowledge(alert_id, session_id)` — inserts `alert_acks`; second ack → `conflict`.
- `FlagService.set(flag, active, reason, actor, session_id=None)` — updates `system_flags`, inserts `system_flag_events`, raises an alert on activation and on clear; `NOTIFY flags`.
- `entries_allowed() -> tuple[bool, str | None]` — false if `kill_switch` or `entries_blocked` active (reason returned for disabled card text).
- Flag reads are uncached (single indexed PK read) so a flag set is visible immediately to `submit_order()` (T5a).

## Out of scope
Push delivery (F029), alert UI (F058), kill switch endpoint (F047).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/alerts/service.py` | raise/ack | HTTP, push |
| `backend/app/alerts/repo.py` | inserts, list queries (used by F030) | policy |
| `backend/app/system/flags.py` | `FlagService` | — |
| `backend/tests/integration/test_alerts_flags.py` | tests | — |

## State transitions
Flag: `inactive ⇄ active` (idempotent: setting the current value records nothing and raises nothing).

## Concurrency / idempotency
`dedupe_key` for recurring conditions (e.g. `feed_silence:{episode_started_at}`) prevents alert storms.

## Tests (integration)
- Raising with the same dedupe key twice → one row.
- Setting kill switch → `entries_allowed()` false with reason; clearing → true; each transition one `system_flag_events` row + one alert.
- Ack twice → conflict.
- App role cannot update `alerts`.

## Acceptance criteria
Flags and alerts are durable, audited, idempotent, and readable within the same transaction as order submission.

## Implementation steps
1. Repo + service for alerts. 2. `FlagService`. 3. NOTIFY emission. 4. Tests.
