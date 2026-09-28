# F011 — Ingest Webhook & Inbox

## Objective
Accept message events from the user's Message API durably and exactly once, and expose the inbox transport boundary (`accept`, `next_for_provider`, `mark`) that everything downstream uses.

## Requirements covered
- Decision log: C8 (messages cross-reference; reply-to captured).
- PRD FR-1 (persist create/edit/delete exactly once keyed by message ID), FR-1d (accepted only once durably stored), FR-3 (reply-to captured), §6 (post time from Discord ID), §11 (DB unavailable → intake refused so the API redelivers), AC-21 (no duplicates).
- TRD §2.4 (transport boundary, webhook contract: HMAC, `INSERT … ON CONFLICT DO NOTHING`, COMMIT, NOTIFY, 200 after commit, 503 when Postgres is unavailable), §7.1, §10 (HMAC + timestamp window).

## Dependencies
- Depends on: F003, F007
- Produces: `Envelope`, `inbox.accept()`, `inbox.next_for_provider()`, `inbox.mark()`, `POST /ingest`, `NOTIFY inbox`.
- Used by: F012, F013, F014, F050.

## Scope
```python
class EventKey(NamedTuple): message_id: int; event_type: Literal["create","edit","delete"]; event_seq: int
class Envelope(BaseModel, frozen=True):
    key: EventKey; provider_id: int; channel_id: int; posted_at: datetime; received_at: datetime
    reply_to_id: int | None; mentioned_author_ids: tuple[int, ...]; text: str | None
    edited_at: datetime | None; backfilled: bool; payload: dict[str, Any]
async def accept(session, env: Envelope) -> bool      # T1; False = duplicate
async def next_for_provider(session, provider_id: int) -> Envelope | None
async def mark(session, key: EventKey, status: InboxStatus, *, error: str | None = None, skip_reason=None, late=None) -> None
```
- **Provisional payload mapping** (`ingest/adapter.py`) — the real Message API payload is unknown (OD-1/OD-2). The adapter accepts this provisional JSON and is the only file F050 replaces:
  `{"type": "message.create|message.update|message.delete", "message": {"id", "channel_id", "author": {"id"}, "content", "timestamp", "edited_timestamp", "reply_to_id", "mentions": [author ids]}}`.
- `posted_at` rules: create = `snowflake_to_datetime(message_id)`; **edit/delete = event time** (`edited_timestamp` / delete time) so a correction is sequenced after the messages posted before it (**OD-24**, proposal); `event_seq` for edits = edit timestamp in ms (C-EVSEQ, provisional).
- Channels: every event is stored (audit); channel/mute filtering happens in the dispatcher (F012) so nothing is dropped silently.

## Out of scope
Gap backfill (F014), heartbeat endpoint (F013), real adapter (F050), processing (F012+).

## Architecture
`POST /ingest` → verify HMAC on the raw body → adapter → `Envelope` → `accept()` in one transaction T1 (`message_events` + `inbox` + `stage_timings(received)` + `NOTIFY inbox, '<provider_id>'`) → commit → 200. Any DB error → 503 (never 200 before commit).

## API contract
| Method | Path | Request | Response | Auth | Service | Codes |
|---|---|---|---|---|---|---|
| POST | `/ingest` | provisional payload (raw JSON) | `{"accepted": bool}` (false = duplicate) | HMAC-SHA256 of `timestamp + "." + body` in `X-Signature`, `X-Timestamp` within ±300 s; secret `Settings.ingest_hmac_secret` | `inbox.accept` | 200 stored/duplicate; 401 bad signature/stale timestamp; 422 unmappable payload (logged + stored raw? no: nothing stored — the API will redeliver; urgent alert raised); 503 DB unavailable |

**422 case:** a payload that cannot be mapped is a contract problem, not a message loss we can fix by retrying; raise an urgent `alerts(kind='ingest_contract')` (best effort) and return 422 so the sender logs it. Recorded as **OD-2** follow-up.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/inbox/envelope.py` | `EventKey`, `Envelope` | DB code |
| `backend/app/inbox/repo.py` | `accept`, `next_for_provider`, `mark`, backlog queries | interpretation |
| `backend/app/ingest/adapter.py` | provisional payload → `Envelope` (pure) | HTTP, DB |
| `backend/app/ingest/security.py` | HMAC + timestamp verification (constant-time compare) | — |
| `backend/app/api/routes/ingest.py` | thin route | business logic |
| `backend/tests/unit/ingest/test_adapter.py`, `test_security.py` | tests | — |
| `backend/tests/integration/inbox/test_inbox_repo.py`, `backend/tests/api/test_ingest_api.py` | tests | — |
| `backend/tests/fixtures/messages/*.json` | synthetic provisional payloads (AC-18 set A1/B2/A3/B4 etc.) | real messages |

## Files to modify
`app/config.py` (`ingest_hmac_secret: SecretStr`), `app/main.py` (router).

## Error cases
Bad signature → 401 + `logins`? No — log + counter only (not a login). DB down → 503. Duplicate → 200 `accepted=false`.

## Security
HMAC over raw bytes before parsing; timestamp window blocks replay; no auth cookie accepted on `/ingest`.

## Concurrency / idempotency
`ON CONFLICT DO NOTHING` on PK; concurrent duplicate deliveries → exactly one row (test with `asyncio.gather`).

## Tests
- Unit: adapter maps create/edit/delete; snowflake → posted_at; edit posted_at = edit time; HMAC valid/invalid/expired.
- Integration: accept returns True then False; concurrent duplicates; `NOTIFY` received by a listener only after commit; `next_for_provider` returns snowflake order even when inserted out of order.
- API: 200/401/422/503 (503 simulated by disposing the engine).

## Acceptance criteria
- A duplicate webhook delivery yields one `message_events` row and one inbox row (TRD §12 sequencing table "Duplicate webhook delivery").
- No 200 is returned before the commit.

## Implementation steps
1. Envelope types. 2. Adapter (pure) + tests. 3. HMAC verifier + tests. 4. Repo functions + integration tests. 5. Route + API tests. 6. Fixtures for AC-18 messages.

## Open decisions touched
OD-1, OD-2, OD-24, C-EVSEQ.

## Implementation notes (2026-09-28)
- `accept`/`claim`/`mark` join the caller's transaction (repo rule: callers own transactions); the route wraps `accept` in T1 via `app.db.in_transaction`. Extra repo helpers for F012: `claim`, `reset_in_flight`, `providers_waiting`, `later_applied`.
- `next_for_provider` returns only `received` events (the dispatcher resets `processing` at startup); it still uses the partial index.
- Delete time: payload `deleted_timestamp` if present, else receive time (provisional, OD-2).
- `event_seq` widened to bigint (migration `0008`): epoch-ms edit sequence overflowed `integer`.
- Contract alert kind is `message_api` (migration `0007`), deduped per payload hash so redeliveries do not storm.
- DB-down 503 is tested by pointing the app's session factory at a dead port (disposing an engine just reconnects). API tests live in `tests/integration/api/`.
- AC-18 payloads: `tests/fixtures/messages/ac18_take_half.json`; helpers in `tests/messages.py`.
