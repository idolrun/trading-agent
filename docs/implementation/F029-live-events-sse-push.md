# F029 — Live Events: SSE & Web Push

## Objective
Push state changes to the open dashboard in real time (SSE) and to desktop/phone (Web Push) — cards, fills, automatic actions, alerts, risk-limit hits — with disabled cards never pushed.

## Requirements covered
- PRD FR-25 (responsive dashboard with push notifications), FR-26 (push for new cards, fills and automatic actions, alerts, risk limit hit; disabled cards do not push), C21, C58, C75, §12 latency (card displayed on an open dashboard ≤ 5 s p95; phone push best effort; timing logged per stage).
- TRD §2.1 `notify` (SSE + Web Push), §3 (Web Push VAPID, `pywebpush`; iOS needs Home-Screen PWA), §15.2 (LISTEN/NOTIFY fan-out).

## Dependencies
- Depends on: F009, F026
- Produces: `EventBus` (one dedicated LISTEN connection → per-subscriber asyncio queues), `GET /api/v1/events` (SSE), push subscription endpoints, `PushService.send(event)`, post-commit hook `notify_after_apply`.
- Used by: F035 (`notify` node), F036, F040–F047, F054.

## Scope
- NOTIFY channels: `cards`, `alerts`, `flags`, `orders`, `fills`, `positions`, `lots`, `feed`. Payload = small JSON `{type, id}` (≤ 8000 bytes; IDs only). Clients refetch via the read API (F030).
- SSE event names: `card.created`, `card.updated`, `alert.created`, `flag.changed`, `order.updated`, `fill.created`, `position.updated`, `lot.updated`, `feed.message`. Built with `fastapi.sse.EventSourceResponse` (auto keep-alive ping every 15 s). Slow client queue bound 1,000 → drop the subscriber (client reconnects and refetches).
- Web Push: VAPID keys in `Settings` (`vapid_private_key: SecretStr`, `vapid_public_key`, `vapid_subject`). `pywebpush` is synchronous → run via `asyncio.to_thread`. On 404/410 → set `revoked_at`. Push rules:
  | Event | Push? |
  |---|---|
  | card created, `state='pending'` | yes (title: author + action + underlying) |
  | card created, `state='disabled'` | **no** (C75) |
  | fill | yes |
  | order with `auto_reason` (reprice excluded) | yes |
  | alert `urgent`/`warning`; `risk_limit` | yes |
- `stage_timings(pushed)` written after SSE fan-out of `card.created`.

## API contract
| Method | Path | Request | Response | Auth | Codes |
|---|---|---|---|---|---|
| GET | `/api/v1/events` | — | `text/event-stream` | session | 401 |
| GET | `/api/v1/push/vapid-public-key` | — | `{publicKey}` | session | |
| POST | `/api/v1/push/subscriptions` | `{endpoint, keys:{p256dh, auth}, label}` | `{subscriptionId}` 201 | session | 422 |
| DELETE | `/api/v1/push/subscriptions/{id}` | — | 204 | session | 404 |

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/notify/bus.py` | LISTEN connection, fan-out, reconnect | business rules |
| `backend/app/notify/push.py` | push rules + sender | card logic |
| `backend/app/notify/hooks.py` | post-commit hook emitting push decisions | — |
| `backend/app/api/routes/events.py`, `backend/app/api/routes/push.py`, `backend/app/api/schemas/push.py` | thin routes | — |
| `backend/tests/integration/notify/test_bus.py`, `test_push_rules.py`, `backend/tests/api/test_events_api.py` | tests (pywebpush mocked at the boundary) | — |

## Files to modify
`app/main.py` (bus lifecycle), `app/pipeline/hooks.py` (register), `app/config.py` (VAPID).

## Security
SSE requires session; payloads carry IDs only; VAPID private key is a secret.

## Tests
- A committed `NOTIFY cards` reaches two SSE subscribers; uncommitted does not.
- Disabled card → no push call; pending card → one push; 410 → subscription revoked.
- Bus reconnects after connection drop and resumes delivery.

## Acceptance criteria
FR-26 push matrix proven; SSE delivers within the test's 1 s budget.

## Implementation steps
1. `uv add pywebpush`. 2. Bus. 3. SSE route. 4. Push service + rules. 5. Subscription routes. 6. Hooks. 7. Tests.
