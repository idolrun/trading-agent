# F057 — Book: Provider Positions, Lots, Orders, Fills

## Objective
Views of both ledgers and execution: provider positions (per author), the user's lots (tracked and foreign, with the "system average" label), orders with state history, and fills.

## Requirements covered
- PRD FR-25 (provider positions, lots tracked and foreign, orders and fills), C47 (foreign read-only), C72, TRD §16.2 #10 (average label), §14 (order state history visible).

## Dependencies
- Depends on: F054 (backend F030)
- Produces: `/positions`, `/positions/[id]`, `/lots/[id]`, `/orders`, `/orders/[id]` routes; extends existing `BookPanel` and `OrderList`.
- Used by: F059, F062.

## Routes / components
| Route | Content |
|---|---|
| `/positions` | positions grouped by provider; status filter; corrected badge |
| `/positions/[id]` | history events, linked signals (method + confidence), lots |
| `/lots/[id]` | qty, avg (labelled "system average — may differ from broker cost basis"), stops/TPs, orders; foreign lots marked read-only |
| `/orders` | status filter; mode badge (shadow/paper/live); auto-reason badge |
| `/orders/[id]` | state history, fills |
All client components using `features/{positions,lots,orders}` queries (existing modules extended).

## States
Loading/empty/error via `QueryState`; live refresh via SSE.

## Files to create / modify
Modify `frontend/features/{positions,lots,orders}/{types.ts,queries.ts}`, `features/desk/book-panel.tsx`, `features/orders/order-list.tsx`; create detail components + pages under `app/(desk)/…` + tests.

## Tests
Foreign lot shows no action buttons; average label present; order history renders in order.

## Acceptance criteria
All FR-25 ledger/order views reachable from the desk.

## Implementation steps
1. Extend queries/types. 2. List pages. 3. Detail pages. 4. Tests.
