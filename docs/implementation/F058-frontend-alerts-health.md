# F058 — Alerts & System Health

## Objective
Alert list with acknowledgement, a global "degraded" banner (feed silence, parser degraded, catch-up, entries blocked, kill switch), and a health page showing the F049 metrics.

## Requirements covered
- PRD FR-4 (dashboard marked "degraded"), FR-1c (catch-up shown until backlog clears), FR-9 ("parser degraded" alert), FR-25 (alerts, system health), §16 (metrics), AC-8 (UI part), AC-21 (catch-up shown).

## Dependencies
- Depends on: F054 (backend F030, F049)
- Produces: `/alerts`, `/health` routes, `<SystemBanner>` in desk layout; extends existing `AlertList` and `FeedStatus`.
- Used by: F062.

## Components
- `SystemBanner`: reads `/api/v1/health/details` flags; urgent styling for `feed_degraded`, `entries_blocked`, `kill_switch`; info for `catch_up`, `parser_degraded`; refreshed by `flag.changed` SSE.
- `AlertList` (existing): severity filter, acknowledge button (`POST /alerts/{id}/acknowledge`), link to related card.
- `HealthScreen`: tables for stage latency p50/p95/p99 (fast/slow), queue wait per provider, backlog, counts, LLM providers (latency, errors, breaker, spend vs budget), card outcomes, order states, reconciliation, invariant blocks. Plain tables (no chart library unless needed later).

## States
Loading/error/empty; stale data indicator from `QueryState`.

## Accessibility
Banner uses `role="status"` / `role="alert"` for urgent; numbers with units.

## Files to create / modify
Create `frontend/features/health/{system-banner.tsx,health-screen.tsx}`, `frontend/app/(desk)/{alerts,health}/page.tsx`; modify `features/alerts/*`, `features/health/{types.ts,queries.ts}`, desk layout; tests.

## Tests
Banner appears for each flag; ack removes from unacknowledged filter; health tables render fixture metrics.

## Acceptance criteria
Degraded and catch-up states visible within one SSE event; alerts acknowledgeable.

## Implementation steps
1. Health types/queries. 2. Banner. 3. Alerts page. 4. Health page. 5. Tests.
