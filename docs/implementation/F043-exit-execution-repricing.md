# F043 — Exit Execution, Re-pricing & Stop Restore

## Objective
Execute approved exits/trims/closes: first reduce or cancel protective orders (FR-20(3)), then a limit at the mid re-priced toward the bid every few seconds until filled or the floor is reached; at the floor stop and notify; if the exit ends unfilled, restore the protective orders.

## Requirements covered
- PRD 7.3 steps 5–6, C25, C35, FR-20(3), FR-21, §9 (re-pricing within an approved intent and stop restore after an unfilled exit are pre-approved), §11 (exit at floor unfilled → notify + restore stops), AC-3 ("unfilled exit restoring the stop", "stop firing after a manual exit").

## Dependencies
- Depends on: F040, F042
- Produces: `ExitPolicy.execute(lot, qty, approval | auto_reason)`, `RepriceLoop` (asyncio task per exit intent), restart adoption hook for F046.
- Used by: F044, F045, F047.

## Scope
1. `reduce_protection(lot, qty)` (F042); wait for cancel acknowledgements (bounded wait; if a stop fires meanwhile the lot shrinks → recompute qty ≤ lot, possibly 0 → stop with notice).
2. Submit `limit` at `round_to_tick(mid)`; loop every `exit_reprice_interval_s`: price ← `max(floor, price − step)` where `step = exit_reprice_step_pct × (mid − bid)`; replace = cancel + new child order (`parent_order_id`, `auto_reason='reprice'`, deterministic `client_order_id`).
3. Floor reached and still unfilled → stop, alert `exit_floor` + push, `restore_protection(lot)` for remaining qty.
4. Partial fills: continue for remainder; on completion restore protection for any remaining lot qty.
5. Floor definition: `exit_floor_pct` below the mid at exit start (**OD-20**, proposal; never below one tick).
6. Restart: F046 adopts working exit orders and restarts their loops from DB state (last price, step count).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/exit.py` | `ExitPolicy`, `RepriceLoop` | invariants (F028 enforces) |
| `backend/app/execution/pricing.py` | pure price path functions (mid, step, floor, tick) | I/O |
| `backend/tests/unit/execution/test_pricing.py`, `backend/tests/scenarios/test_exit_execution.py` | tests | — |

## Files to modify
`app/execution/card_executor.py` (exit/trim/close), `app/execution/consumer.py` (exit fill hook).

## Tests (AC-3 scenarios)
- Unfilled exit reaches floor → stop restored, alert.
- Stop fires while exit waits for cancel ack → exit qty recomputed, no oversell.
- Manual exit then broker stop fires → invariant prevents oversell (stop was reduced first).
- Pricing: monotone non-increasing, never below floor, tick-aligned (property test).

## Acceptance criteria
AC-3 exit/restore clauses proven on fakes; every reprice order carries `auto_reason='reprice'` and a parent.

## Implementation steps
1. Pure pricing. 2. Reduce → submit → loop. 3. Floor + restore. 4. Partial handling. 5. Restart adoption hook. 6. Tests.

## Open decisions touched
OD-20.
