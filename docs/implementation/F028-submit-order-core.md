# F028 — `submit_order()` Core & Invariants

## Objective
The single function through which every order reaches the broker: runs the FR-20 safety invariants, commits the order intent with a deterministic client order ID **before** calling the broker, handles timeouts by status query (never blind retry), and supports shadow / paper / live modes.

## Requirements covered
- PRD FR-20 (1) never open a short, (2) sell qty ≤ contracts in that provider's lot (manual exits included), (3) exits reduce/cancel working stops and TPs (the reduce step itself is F043; this function refuses a sell that would over-commit the lot), FR-22 (system client order ID; on timeout query before any retry), §9 (pre-approved automatic actions), AC-2, AC-3, AC-7, §17 shadow ("orders it would have placed").
- TRD §1.5 (every order through one function), §5.6 (`client_order_id = uuid5(card_id, action_seq)`), §6 invariants checked with a fresh broker position read, §9 order idempotency, §12 property tests.

## Dependencies
- Depends on: F008, F010, F022, F025
- Produces: `submit_order(intent: OrderIntent, auth: Approved(approval_id) | Auto(reason)) -> Order`; `cancel_order(order_id, reason)`; `ac2_violations(session) -> list[UUID]`; `SimulatedBroker` for shadow mode.
- Used by: F036, F041–F047.

## Scope
```python
class OrderIntent(BaseModel): card_id: UUID | None; action_seq: int; parent_order_id: UUID | None
    kind: Literal["entry","exit","stop","tp"]; side; instrument: Instrument; qty: int
    order_type; limit_price; stop_price; tif; provider_position_id: UUID | None; lot_id: UUID | None; oco_group_id: UUID | None
```
Steps:
1. **T5a** (short tx): read flags — entries blocked or kill switch → refuse entries (`EntriesBlocked`); for sells `lock_lot(lot_id)`.
2. Invariants (pure, `execution/invariants.py`):
   - (1) `side=sell` requires `lot_id` of an open lot with the **same instrument**; buy only for `kind=entry` (also DB CHECK).
   - (2) `qty + Σ working non-protective sell orders on this lot ≤ lot.qty`; and `Σ working protective (stop+tp) qty ≤ lot.qty` after this order (protective orders may be reduced first by F043).
   - Fresh broker read: `broker.positions()` qty for the symbol ≥ sell qty; otherwise refuse + `invariant_block` alert (the broker is the source of truth, PRD §11).
   Violation → `InvariantViolation` (no row written), alert `invariant_block`, counter.
3. Insert `orders(status='intent', client_order_id, mode, approval_id | auto_reason)` + `order_events(submit)`; commit.
4. Mode `shadow` → `SimulatedBroker` (fills at the current quote via the fake-broker engine; never calls a real broker; **OD-28:** confirm simulated fills in shadow so exit paths can be exercised); `paper`/`live` → configured `BrokerClient`.
5. Call broker **outside** any transaction. Outcomes (T5b):
   - accepted → `submitted`/`working` + `broker_order_id`;
   - `BrokerRejected` → `rejected` + alert;
   - `BrokerUnavailable` → `rejected` + alert (definitely not sent);
   - `BrokerTimeout` → `unknown` → `get_by_client_id`: found → adopt status; not found → `rejected` (intent void) + alert — **never resubmitted blindly** (FR-22).
- `client_order_id = "cta-" + uuid5(NS, f"{card_id}:{action_seq}")`; auto orders: `uuid5(NS, f"{parent_order_id}:{auto_reason}:{seq}")`. A re-executed graph node computes the same ID → UNIQUE conflict → return the existing order (AC-7).
- `ac2_violations()`: orders lacking both approval and allowed auto reason (the DB CHECK makes this empty; the query is the audit artifact for PRD AC-2).

## Out of scope
Pricing policy (F041/F043), fill processing (F040), reduce/restore of stops (F043), brackets (F042).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/intent.py` | `OrderIntent`, auth types, ID derivation | I/O |
| `backend/app/execution/invariants.py` | pure invariant checks | I/O |
| `backend/app/execution/submit.py` | `submit_order`, `cancel_order` | pricing, card logic |
| `backend/app/execution/repo.py` | order/order_event queries, working-sell sums | decisions |
| `backend/app/broker/simulated.py` | shadow-mode broker built on the fake engine | — |
| `backend/app/execution/audit.py` | `ac2_violations` | — |
| `backend/tests/unit/execution/test_invariants.py` (+ hypothesis: random sequences of fills/sells never produce negative lot or short) | tests | — |
| `backend/tests/integration/execution/test_submit_order.py` | timeout/unknown/duplicate/flags tests | — |

## Concurrency / idempotency
Lot row lock during checks + intent insert; UNIQUE `client_order_id`; broker call after commit.

## Tests
- Cross-lot sell attempt (sell provider B's contracts against provider A's lot) → violation (AC-3).
- Sell more than lot → violation; sell with protective orders over-committing → violation.
- Timeout then exists → adopted, no second submit (FakeBroker call count = 1); timeout then missing → rejected + alert, no resubmit.
- Same intent twice → one order row, one broker call (AC-7 building block).
- Kill switch active → entry refused, exit allowed.
- Shadow mode never touches the real broker adapter (assert adapter not called).

## Acceptance criteria
AC-3 invariants (1)(2) proven by unit + property tests; FR-22 timeout behaviour proven; AC-2 audit query empty.

## Implementation steps
1. Intent + ID derivation. 2. Pure invariants + property tests. 3. Repo. 4. `submit_order` flow. 5. SimulatedBroker. 6. Audit query. 7. Integration tests.

## Open decisions touched
OD-28.

## Implementation notes (2026-09-29)
- `OrderService.submit_order(intent, Approved | Auto)` / `cancel_order` in `execution/submit.py`; brokers per mode: shadow → `SimulatedBroker` (OD-28 confirmed: fills marketable limit/market orders at the quote; stops rest), paper/live → the injected `BrokerClient`. Wired as `app.state.orders`.
- Order of checks: existing client id → return it (AC-7, no broker call); entries → `FlagService.entries_allowed` (`EntriesBlocked`); sells → fresh `broker.positions()` read **before** the transaction; T5a locks the lot, sums working sells, runs the pure invariants, inserts `intent` + `order_events(submit)`, commits.
- Invariant codes: `buy_only_for_entries`, `entry_must_buy`, `no_open_lot`, `instrument_mismatch`, `other_providers_lot` (intent's provider position ≠ lot's — AC-3 cross-provider), `sell_exceeds_lot` (qty + working exits), `protective_exceeds_lot` (working stops/TPs + qty), `broker_position_short` (broker qty < sell qty or unreadable). Violation → `invariant_block` alert, no row.
- Broker failures raise the new alert kind **`order_failed`** (migration `0010`, database-schema.md #28): rejected / unavailable → `rejected`; timeout → `unknown` → `get_by_client_id` adopts or voids (`rejected`), never resubmits.
- Fills are not written here (F040 consumes broker events); `filled_qty` is copied from the broker's answer.
- The hypothesis property checks what `submit_order` guarantees at acceptance; a stop still working after an exit fills is F043's reduce step (FR-20(3)).
