# F042 — Stops, Take-Profits & Brackets

## Objective
Place and maintain the protective orders the user approved: broker-held premium stops, stock stop-market GTC, take-profit limit legs split per C83, linked so one cancels the other; plus approved stop set/move/cancel cards and breakeven choice.

## Requirements covered
- PRD 7.4 (all bullets), FR-23, C28, C29, C49 (system-held underlying stops → F044), C50, C56, C61, C83, AC-16, §9 (broker-held stop/TP firing after approval is pre-approved).
- TRD §6 TP legs, §7.2 (broker OCO/bracket capability — OD-3).

## Dependencies
- Depends on: F036, F040
- Produces: `protect_lot(lot, bracket)`, `StopPolicy` for stop/tp cards, `reduce_protection(lot, qty)` / `restore_protection(lot)` (used by F043), `stops` + `stop_events` maintenance.
- Used by: F041, F043, F044, F045, F047.

## Scope
- **Bracket placement** (after entry fills): stop leg (premium → `stop` order at broker; stock → `stop_market` GTC; underlying → `stops` row `held_by='system'` only, F044 watches) + TP legs (`limit` sells, qty per C83 over filled qty), grouped by `oco_group_id`. Broker-native OCO when the adapter supports stop-vs-TP OCO; otherwise system-managed linkage.
- **Linkage maintenance (OD-30, proposal):** invariant Σ protective qty ≤ lot qty. When a TP leg fills, reduce the stop qty by the filled amount (cancel/replace); when the stop fills, cancel remaining TPs. This maintenance is part of the approved bracket intent (orders carry `approval_id` of the bracket card; replacements use `parent_order_id`). Needs confirmation that it counts as within approval (PRD §9 lists "a broker-held stop or TP firing after approval").
- **Stop cards:** `set` → place; `move` → replace (cancel + new, or native replace); `cancel` ("pulled stop") → cancel. Each only after approval (7.4). Breakeven: approved `breakevenChoice` selects `user_avg` or `provider_entry` as level (C29).
- **Candle-close conditions:** info only on the card; stop placed at the level (C49).
- `reduce_protection(lot, exit_qty)`: before an exit, cancel/resize protective orders so that protective + exit ≤ lot (FR-20(3)); records `suspended` in `stops`.
- `restore_protection(lot)`: re-place suspended protection for the remaining lot qty (auto_reason `stop_restore`, pre-approved §9).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/brackets.py` | placement, linkage maintenance, reduce/restore | decide logic |
| `backend/app/execution/stops_repo.py` | `stops`/`stop_events` queries | — |
| `backend/tests/scenarios/test_brackets.py` | tests | — |

## Files to modify
`app/execution/card_executor.py` (stop/tp actions), `app/execution/consumer.py` (TP/stop fill hooks).

## Tests
- 2 contracts, 3 TPs → TP1 1, TP2 1, stop 2 (AC-16); TP1 fills → stop resized to 1; stop fills → TP2 cancelled.
- Stock entry → stop-market GTC.
- Stop move card → old stop cancelled, new stop working; no gap longer than one broker round trip (documented).
- Breakeven choice honoured.

## Acceptance criteria
Protective qty never exceeds lot qty (property test); AC-16 at execution level.

## Implementation steps
1. Stops repo. 2. Placement. 3. Linkage maintenance. 4. Stop card policies. 5. Reduce/restore. 6. Tests.

## Open decisions touched
OD-3, OD-30.
