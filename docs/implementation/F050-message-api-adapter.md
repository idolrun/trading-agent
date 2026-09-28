# F050 — Real Message API Adapter (**blocked on S1 / OD-1, OD-2**)

## Objective
Replace the provisional ingest mapping and fake history client with the user's real Message API: payload mapping, transport (webhook or websocket), heartbeat, edit/delete events, history/backfill client and historical export import.

## Requirements covered
- PRD FR-1, FR-1d, FR-3, FR-4 (heartbeat), §15 Message API needs (sample payload, fields, heartbeat, historical export, stable IDs, delivery semantics, ordering), C11, C13, C65, C91, AC-21.
- TRD §7.1 week-0 spike, §2.4 (local spool if neither redelivery nor backfill).

## Dependencies
- Depends on: F011, F014 (and spike S1 answers recorded in README §10.3)
- Produces: real `ingest/adapter.py` mapping, `messageapi/client.py` implementing `MessageHistory`, optional websocket consumer, export importer mapping for F048.
- Used by: go-live (shadow stage).

## Unblock checklist (fill from S1 before coding)
- [ ] Sample payloads for create/edit/delete saved to `backend/tests/fixtures/messageapi/` (synthetic or scrubbed — no secrets).
- [ ] Transport: webhook with HMAC (keep F011 route) **or** websocket (add consumer task calling `inbox.accept`).
- [ ] Redelivery ≥ 10 min on non-2xx? History API by channel after message ID? At least one must be true; else design a local append-only spool **before** coding (TRD §7.1).
- [ ] Edit sequence field → replaces C-EVSEQ provisional rule.
- [ ] Heartbeat format → F013.
- [ ] Per-channel ordering guarantee (informational; sequencing already by snowflake).
- [ ] Author/message IDs are Discord snowflakes (schema assumption).

## Scope
Only the adapter layer changes; `Envelope`, `inbox`, dispatcher and everything downstream stay untouched (transport boundary, TRD §2.4).

## Files to create / modify
| File | Change |
|---|---|
| `backend/app/ingest/adapter.py` | real mapping (keep provisional mapping only in tests if still useful) |
| `backend/app/messageapi/client.py` | HTTP history client (httpx, timeouts, retries on idempotent GETs) |
| `backend/app/messageapi/ws.py` | only if websocket transport |
| `backend/app/evalkit/import_export.py` | real export format |
| `backend/tests/contract/test_messageapi_payloads.py` | contract tests against saved payloads |

## Tests
Contract tests for every event type; gap backfill against a recorded history response; AC-21 rerun with the real client in a staging channel (manual, documented).

## Acceptance criteria
Real payloads map to `Envelope`; backfill works against the real API; provisional assumptions removed from README §10.3.

## Implementation steps
1. Complete unblock checklist. 2. Mapping + contract tests. 3. History client. 4. Transport. 5. Export importer. 6. Update README decisions.
