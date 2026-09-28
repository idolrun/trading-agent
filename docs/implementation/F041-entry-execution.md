# F041 — Entry Execution

## Objective
Execute an approved entry/add exactly per C22: limit at signal price (top of range) + X%, cancel any unfilled remainder after M seconds, keep partial fills, and place approved bracket legs for what actually filled.

## Requirements covered
- PRD 7.1 step 6–7, FR-21 (entry pricing and cancellation per C22), C22, C50 (bracket at entry), UC1, UC2, UC6 (adds), AC-4 (no order when invalid — enforced at revalidate), §9 (entry needs approval).

## Dependencies
- Depends on: F036, F040
- Produces: `EntryPolicy` in `CardExecutor`, timer handler `entry_cancel`, fill hook placing brackets via F042.
- Used by: F047 (manual entry), F062.

## Scope
1. From the approved proposal: `OrderIntent(kind='entry', side='buy', order_type='limit', limit_price=approved_limit, qty, tif='day')` → `submit_order(approved)`.
2. Schedule `entry_cancel` at `submitted_at + entry_cancel_after_s` (`dedupe_key=entry_cancel:{order_id}`).
3. Timer: if order `working`/`partially_filled` → `cancel_order` (remainder); partial fills stay in the lot (C22). Alert info "entry partially filled n/m".
4. On each fill (F040 hook): if the approved proposal has bracket legs → ask F042 `protect_lot(lot, proposal)` to place/resize stop + TPs for the **filled** qty (TP legs recomputed with C83 on filled qty).
5. Add cards: same policy; lot grows; protective orders resized to new lot qty (F042).
6. Stocks: limit at price + X%; stop-market GTC per C61 via F042.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/entry.py` | `EntryPolicy`, cancel timer handler | invariants (F028) |
| `backend/tests/scenarios/test_entry_execution.py` | tests | — |

## Files to modify
`app/execution/card_executor.py` (use `EntryPolicy`), `app/timers/service.py` (register), `app/execution/consumer.py` (hook).

## Tests
- Full fill → lot qty = qty; bracket legs placed for qty.
- Partial 1 of 3 then M s → remainder cancelled, lot 1, bracket for 1 (TP1 only).
- No fill → cancelled, no lot, no bracket.
- Add → lot grows, stop qty resized.

## Acceptance criteria
UC1/UC2 on fakes; C22 remainder cancellation proven.

## Implementation steps
1. Policy. 2. Cancel timer. 3. Fill hook → F042. 4. Tests.
