# F013 — Feed Health & Catch-up Mode

## Objective
Detect feed silence during market hours (urgent alert + "degraded" state) and detect backlog (catch-up mode alert until drained).

## Requirements covered
- Decision log: C41.
- PRD FR-4 (no message and no heartbeat for N s in market hours → urgent alert + dashboard "degraded"), FR-1c (catch-up mode + alert until backlog clears), AC-8, §11 feed silence.
- TRD §2.4 (catch-up when backlog > N events or oldest unprocessed > `stale_entry_s`), §11.

## Dependencies
- Depends on: F012 (and transitively F008 flags, F007 market hours, F006 config)
- Produces: `FeedHealthMonitor` (lifespan task), `POST /ingest/heartbeat`, flags `feed_degraded`, `catch_up`.
- Used by: F014 (degraded→recovered triggers backfill), F030/F049 (health), F058.

## Scope
- Heartbeat endpoint: HMAC as `/ingest`; records `last_heartbeat_at` in memory and writes `stage`-less metric (no table needed).
- Monitor tick every 1 s:
  - `last_signal = max(last_message_received_at, last_heartbeat_at)`; at startup both default to process start (grace period = `feed_silence_s`).
  - If `is_regular_hours(now)` and `now − last_signal > feed_silence_s` → `FlagService.set('feed_degraded', True)` + urgent alert (dedupe per episode). Clears on next message/heartbeat (+ info alert, and triggers the F014 backfill hook).
  - Backlog: `count(inbox WHERE status='received')` and oldest `posted_at`; if `count > catch_up_backlog_n` or `now − oldest > stale_entry_s` → set `catch_up`; clear when both below thresholds.
- If `feed_silence_s` or `catch_up_backlog_n` is unset in config → monitor raises a one-time `warning` alert "feed health not configured" and skips that check (never guesses a value).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ingest/feed_health.py` | monitor + pure `evaluate(now, last_signal, backlog, cfg) -> FeedState` | HTTP |
| `backend/tests/unit/ingest/test_feed_health.py`, `backend/tests/integration/ingest/test_feed_health_monitor.py`, `backend/tests/api/test_heartbeat_api.py` | tests | — |

## Files to modify
`app/api/routes/ingest.py` (heartbeat route), `app/main.py` (start monitor), `app/inbox/repo.py` (backlog query).

## API contract
| POST | `/ingest/heartbeat` | body `{}` | 204 | HMAC | 401 bad signature |

## Tests
- Unit: silence inside/outside regular hours; boundary at exactly N s; catch-up thresholds.
- Integration (FixedClock): no messages for N+1 s during hours → `feed_degraded` active + one urgent alert; a heartbeat clears it; 50 received rows with `catch_up_backlog_n=20` → `catch_up` active, cleared after processing.

## Acceptance criteria
AC-8 proven with a fixed clock; catch-up flag visible via flags and alert.

## Implementation steps
1. Pure `evaluate`. 2. Monitor task. 3. Heartbeat route. 4. Backlog query. 5. Tests.

## Open decisions touched
OD-2 (heartbeat exists?), OD-15.

## Implementation notes (2026-09-28)
- Pure `evaluate()` returns `feed ∈ {silent, ok, hold, None}`: `hold` = quiet outside regular hours (no change; an active `feed_degraded` stays until a message/heartbeat arrives), `None` = `feed_silence_s` unset. Silence is strictly `> feed_silence_s`.
- The urgent alert comes from `FlagService.set(feed_degraded)` (kind `system_flag`, F008) — no second `feed_silence` alert, to avoid duplicates. Same for `catch_up`.
- Unset thresholds raise one `warning` alert per process start (kinds `feed_silence` / `catch_up`) and skip that check.
- `on_recovered` hooks run when `feed_degraded` clears (F014 registers the gap backfill).
- `Settings.dispatcher_enabled` renamed `background_enabled`: it gates dispatcher, heartbeat, feed monitor (and the F014 gap task). The monitor object always exists so `/ingest` and `/ingest/heartbeat` can record signals.
- Heartbeat API tests live in `tests/integration/api/`.
