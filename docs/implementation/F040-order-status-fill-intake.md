# F040 — Order Status & Fill Intake

## Objective
Consume broker order-status and fill events (stream or poll — OD-10) idempotently, update orders, and move the user lot ledger from fills in one transaction per fill.

## Requirements covered
- PRD 7.1 step 7 (lot created and linked to the provider position), FR-16 (adds grow lot, re-entry new lot), FR-21 (partial fills kept), §14 (orders + state history, fills), §11 (broker is source of truth; reconcile after fills), FR-26 (push fills).
- TRD §6 order state machine, §9 (periodic reconciliation after every fill), schema doc T6. OD-10.

## Dependencies
- Depends on: F028, F022
- Produces: `BrokerEventConsumer` (lifespan task), `apply_broker_event(session, event)`, post-commit hooks (SSE/push, reconcile trigger, protective-order hooks used by F041–F042).
- Used by: F041–F047, F049.

## Scope
- Consumer reads `BrokerEvents.events()`; each event processed in its own transaction:
  - **status:** find order by `broker_order_id` or `client_order_id`; apply the order state machine (`IllegalTransition` ignored + logged — events can arrive late/duplicated); insert `order_events(source='broker_stream'|'poll')`.
  - **fill (T6):** `INSERT fills … ON CONFLICT (broker_fill_id) DO NOTHING` — duplicate → stop; lock order + lot `FOR UPDATE`; entry buy fill → `open_lot_for_position` or create lot (sets `orders.lot_id`); sell fill → reduce lot; update `orders.filled_qty/avg_fill_price/status`; `lot_events`; `NOTIFY fills, lots, orders`.
  - Events for unknown orders (not ours) → ignored here; reconciliation (F046) treats resulting positions as foreign.
- Post-commit hooks (registry): `on_fill(order, fill, lot)` — used by F041 (bracket placement), F043 (exit progress), F042 (stop qty maintenance), F046 (reconcile after fill), F029 (push).
- Consumer restarts from broker state via F046 on startup (no persisted cursor needed; events are idempotent).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/fills.py` | `apply_broker_event`, T6 | pricing policy |
| `backend/app/execution/consumer.py` | lifespan task, hook registry | — |
| `backend/tests/integration/execution/test_fills.py` | FakeBroker-driven tests | — |

## Tests
- Entry fill → lot created and linked; second fill (add) → avg recomputed; duplicate fill event → no change.
- Partial fill then cancel → lot keeps partial qty; order `cancelled` with `filled_qty`.
- Out-of-order status events do not regress state.

## Acceptance criteria
Every fill applied exactly once; lot ledger always equals sum of fills (property test over random event streams with duplicates).

## Implementation steps
1. `apply_broker_event` status path. 2. Fill path T6. 3. Consumer task + hooks. 4. Tests incl. property test.

## Open decisions touched
OD-10.
