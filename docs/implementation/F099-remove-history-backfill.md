# F099 — Remove Discord history backfill

## Objective
Stop reading Discord channel history. `DiscordHistory` paged `channel.history()` through the selfbot user account to fill gaps, which counts as automated scraping under Discord's Terms. The gap detector existed only to call it, so the whole gap-backfill subsystem was removed with it.

## Decision (2026-10-13)
- Removed: `app/discord_source/history.py`, `app/ingest/gaps.py` (`GapDetector`), `app/messageapi/` (`MessageHistory` port and fake), `DiscordListener.ready_client`, `FeedHealthMonitor.on_recovered`, startup step 7 and the backfill in `recover_after_failover`, and the periodic `gap-detector` task.
- Accepted loss: a message posted while the listener is disconnected, or one whose storage fails, is gone. Only the live gateway (and `POST /ingest`) feeds the inbox. This supersedes FR-1d's backfill clause, AC-21's "or backfilled", and F014.
- Kept: the `backfilled` flag on `message_events` / `inbox_items` and everything that reads it. `evalkit import` still stores history exports as backfilled, and old rows keep it. No migration.

## Tests
- `test_gap_detector.py` and the Discord history tests in `test_listener.py` deleted.
- `tests/load/test_outage.py`: recovery is webhook redelivery of the whole backlog; the backfill check is gone.
- `tests/load/test_failover.py`: the "posted while down, backfilled at step 7" messages are gone.
