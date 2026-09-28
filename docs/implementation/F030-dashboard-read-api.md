# F030 — Dashboard Read API v1

## Objective
Stable, typed, read-only JSON endpoints for every dashboard view (plus alert acknowledgement), matching and extending the contract the existing frontend already consumes — so the frontend never builds against guesses.

## Requirements covered
- PRD FR-25 views: live message feed, cards, provider positions, lots (tracked and foreign), orders and fills, alerts, system health; FR-10 (non-actionable messages in feed with classification label), FR-1c (stale/late labels), §12 Time (UI shows local time → API returns ISO UTC).
- TRD §11 (health page as SQL over tables — full metrics in F049).

## Dependencies
- Depends on: F009, F026, F028
- Produces: routes and response schemas below; `openapi.json` export command.
- Used by: F052–F058.

## API contract (all `GET` unless stated; auth = session; list endpoints return `{items: [...], nextCursor: string | null}`; cursor = opaque base64 of `(created_at, id)`; `limit` ≤ 200)
| Path | Query | Response item (camelCase) | Service | Errors |
|---|---|---|---|---|
| `/api/v1/feed` | `providerId?, label?, cursor?, limit?` | `FeedItem{messageId, eventType, providerId, channelId, postedAt, text, replyToId, status, labels[stale,late,backfilled,muted,channel_not_configured,failed], msgTypes[], cardIds[]}` | `FeedQuery.list` | 422 |
| `/api/v1/cards` | `state?, providerId?, cursor?, limit?` | `Card{id, messageId, signalIndex, action, cardType, state, providerId, providerPositionId, lotId, underlying, contract, proposal{qty, price, stop, tpLegs[], warnings[], labels[], missingFields[], suggestedUnderlying, candidatePositions[], breakevenOptions}, postedAt, validUntil, note, noteHidden, version, sourceMessages[], inferredFields[]}` | `CardQuery.list` | |
| `/api/v1/cards/{cardId}` | — | `CardDetail` = Card + `events[]` + `approval` | `CardQuery.get` | 404 |
| `/api/v1/positions` | `status?, providerId?` | `ProviderPosition{id, providerId, instrument, underlying, expiry, strike, optionRight, status, isCorrected, providerEntryPrice, remainingFraction, stopLevel, stopBasis, tpLevels[], openedAt}` | | |
| `/api/v1/positions/{id}` | — | + `events[]`, `lots[]`, `links[]` | | 404 |
| `/api/v1/lots` | `status?, foreign?` | `Lot{id, providerPositionId, providerId, contract, qty, avgPrice, avgPriceLabel:"system average", isForeign, status}` | | |
| `/api/v1/lots/{id}` | — | + `events[]`, `stops[]`, `orders[]` | | 404 |
| `/api/v1/orders` | `status?, lotId?` | `Order{id, cardId, lotId, kind, side, status, clientOrderId, qty, filledQty, limitPrice, stopPrice, mode, autoReason, createdAt}` | | |
| `/api/v1/orders/{id}` | — | + `events[]`, `fills[]` | | 404 |
| `/api/v1/fills` | `orderId?, since?` | `Fill{id, orderId, qty, price, filledAt}` | | |
| `/api/v1/alerts` | `acknowledged?, severity?` | `Alert{id, severity, kind, message, acknowledged, cardId, createdAt}` | | |
| `POST /api/v1/alerts/{id}/acknowledge` | — | `Alert` | `AlertService.acknowledge` | 404, 409 |
| `/api/v1/health/details` | — | `Health{status: ok|degraded, feedSilenceSeconds, flags{…}, catchUp, backlogDepth, oldestUnprocessedSeconds, heartbeatAgeSeconds}` (F049 adds latency/LLM stats) | | |
Existing frontend fields (`frontend/features/*/types.ts`) are a subset of the above except card action values (backend uses `entry|add|trim|exit|stop|tp|close|review`, matching the frontend's list plus `review`) — reconciled in F052 by regenerating types.

## Scope
- Query services per area (`app/<module>/queries.py`) returning Pydantic response models; no ORM entities in responses.
- `sourceMessages` = the event text + context messages used (literal spans highlighted via offsets); `inferredFields` = fields with `context` provenance and their source message ids (7.1 step 4).
- OpenAPI export: `uv run python -m app.api.export_openapi > ../frontend/openapi.json` (committed; F052 generates TS types).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/api/routes/{feed,cards,positions,lots,orders,fills,alerts,health}.py` | thin GET routes | SQL |
| `backend/app/api/schemas/{feed,cards,ledger,orders,alerts,health}.py` | response models | — |
| `backend/app/{inbox,cards,ledger,execution,alerts}/queries.py` | read queries + mapping to response models | writes |
| `backend/app/api/pagination.py` | cursor encode/decode | — |
| `backend/app/api/export_openapi.py` | writes `openapi.json` | — |
| `backend/tests/api/test_read_api.py` | tests | — |

## Security
All routes require session; Discord IDs as strings; no secrets in responses.

## Tests
Each endpoint: auth required (401), filters, pagination stability, 404; feed shows stale/muted/failed events (nothing hidden); snapshot test of `openapi.json` to catch unintended contract changes.

## Acceptance criteria
Every FR-25 view has an endpoint; OpenAPI exported; contract snapshot test in place. **Milestone M2** demo: fixtures → cards visible via API + SSE.

## Implementation steps
1. Pagination helper. 2. Query modules. 3. Schemas. 4. Routes. 5. OpenAPI export + snapshot test. 6. Tests.
