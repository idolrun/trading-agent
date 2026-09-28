# F014 — Gap Detection & Backfill

## Objective
Find messages the webhook missed (outage, reconnect, restart) per channel and feed them through `inbox.accept()` tagged `backfilled`.

## Requirements covered
- PRD FR-1d (detect gaps per channel by last seen message ID; backfill on startup, on reconnect, periodically during market hours), AC-21, §11 message-API outage.
- TRD §2.4 gap detector (every 60 s in market hours), §9 startup step 7.

## Dependencies
- Depends on: F011
- Produces: `MessageHistory` Protocol, `FakeMessageHistory`, `GapDetector.run_once()`, periodic task, `on_recovered` hook for F013.
- Used by: F046 (startup step 7), F050 (real history client).

## Scope
```python
class MessageHistory(Protocol):
    async def fetch_after(self, channel_id: int, after_message_id: int | None, limit: int) -> list[dict]  # raw provisional payloads, oldest first
```
- `run_once()`: for each configured channel: `last_seen = max(message_id)` over `message_events` create events for that channel; page `fetch_after` until empty; map with `ingest/adapter.py`; `accept(env.model_copy(update={"backfilled": True}))`; duplicates are no-ops.
- Triggers: startup (called by F046), `feed_degraded` cleared (F013 hook), every 60 s while `is_regular_hours`.
- Edits/deletes that happened during an outage: only if the history API exposes them (**OD-2**); otherwise documented gap (PRD §21).
- If history is unavailable (`MessageHistoryUnavailable`) → warning alert, retry next tick.

## Out of scope
Real client (F050); local spool (only if OD-1 finds neither redelivery nor history).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/messageapi/port.py` | `MessageHistory` Protocol + errors | HTTP details |
| `backend/app/messageapi/fake.py` | in-memory channel histories | — |
| `backend/app/ingest/gaps.py` | `GapDetector` | adapter mapping (reuse F011) |
| `backend/tests/integration/ingest/test_gap_detector.py` | tests | — |

## Files to modify
`app/main.py` (periodic task), `app/ingest/feed_health.py` (call hook on recovery), `app/inbox/repo.py` (`last_seen_per_channel`).

## Concurrency
`run_once` guarded by an `asyncio.Lock` (no overlapping runs); `accept` idempotent.

## Tests
- Fake history has 10 messages; webhook delivered 1–3 and 8–10 → run_once accepts 4–7 with `backfilled=true`, 0 duplicates.
- Second run accepts nothing.
- History failure → warning alert, no crash.

## Acceptance criteria
AC-21 (fake): every message posted during a simulated outage ends up in `message_events` exactly once and is processed; backfilled flag visible.

## Implementation steps
1. Port + fake. 2. `last_seen_per_channel` query. 3. `GapDetector`. 4. Triggers. 5. Tests.

## Open decisions touched
OD-1, OD-2.

## Implementation notes (2026-09-28)
- **Deviation — watermark instead of "max seen id".** `max(message_id)` per channel cannot find a gap that lies *before* newer webhook deliveries (webhook delivered 1–3 and 8–10 → max is 10, so 4–7 are never fetched — this doc's own test). `GapDetector` keeps a per-channel in-memory watermark = newest id already verified against history; each run fetches everything after it. On a process's first run the watermark is the newest create event posted before `now − lookback` (8 h, one full session), so a restart re-verifies that window. `inbox.repo.last_seen_per_channel(channels, posted_before)` provides it. ponytail: watermark not persisted; persist it if startup re-verification becomes costly.
- History unavailable → `message_api` warning alert (deduped per channel per hour), retried next tick. Unmappable history payload → urgent `message_api` alert, skipped.
- Triggers wired: every 60 s during regular hours (`background_enabled`), and on `feed_degraded` recovery (F013 hook). Startup trigger is F046.
- App uses `FakeMessageHistory` until F050.
