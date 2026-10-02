# F067 — Discord event identity + threads

## Objective
Every stored message event keeps its full Discord identity, and messages posted in threads are no longer dropped.

## Before
- `discord_source/listener.py` checked the allowlist against `message.channel.id`. In a thread that is the thread id, which is never in `RuntimeConfig.channels`, so every thread message was dropped silently.
- `guild_id` and `thread_id` were not captured.

## What changed
| Area | Change |
|---|---|
| `discord_source/mapping.py` | `context_channel(channel)`: a `discord.Thread` (has `parent_id`) maps to `(parent_id, thread_id)`; other channels to `(id, None)`. Payload adds `thread_id`, `guild_id`. |
| `discord_source/listener.py` | `handle(payload)` reads the context channel from the payload (allowlist = parent channel). A raw delete names the thread, so `_stored_identity` resolves author, parent channel and thread from the stored event (or the cached message). |
| `ingest/adapter.py`, `inbox/envelope.py` | Optional `thread_id`, `guild_id`; `Envelope.lane = (channel_id, provider_id)`. |
| `inbox/models.py`, `inbox/repo.py` | `message_events.thread_id`, `guild_id`; `inbox.channel_id` (the lane key for F068). |
| Migration `0019` | Columns, CHECK `> 0`, lane indexes, inbox `channel_id` backfilled then NOT NULL. |

## Tests
- `tests/unit/discord_source/test_discord_mapping.py`: thread → parent channel + thread id; plain channel → no thread.
- `tests/integration/discord_source/test_listener.py`: a thread message under a watched channel lands in the inbox with its thread id; a thread delete resolves to the parent; a thread under an unwatched channel is ignored.

## Still open
- **Thread history is not backfilled.** The gap detector pages channel history only; a thread message missed during downtime is not recovered. Upgrade path: page `channel.threads` (see `ponytail:` in `discord_source/history.py`).
- Author display names are not stored (identity is the snowflake; the raw payload is kept).
