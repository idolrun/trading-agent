# F022 — User Lot Ledger

## Objective
Model the user's real holdings as lots linked one-to-one to provider positions (plus read-only foreign lots), updated only from fills and reconciliation.

## Requirements covered
- PRD FR-16 (lots per provider; adds grow the lot and recompute the average; re-entry after full exit = new lot), C24 (two providers on the same contract = two lots), C47 (foreign positions read-only, count toward limits), C62, §6 Lot.
- TRD §4.5 (user-portfolio decisions are code lookups), §16.2 #10 (system average may differ from broker FIFO; UI labels it).

## Dependencies
- Depends on: F020
- Produces: pure `apply_fill(lot | None, side, qty, price) -> LotChange`; repo `open_lot_for_position`, `lock_lot`, `lots_by_underlying`, `open_lots_summary`, `create_foreign_lot`.
- Used by: F023, F024, F028, F040, F046, F047.

## Scope
- Buy fill for an entry/add order of provider position P: open lot for P exists → `qty += q`, `avg = (qty·avg + q·price) / (qty + q)` (Decimal, 4 dp, ROUND_HALF_EVEN); no open lot → create lot (`status=open`).
- Sell fill: `qty −= q` (never below 0 — assertion; invariant already enforced in F028); `avg` unchanged; `qty == 0` → `closed`.
- Foreign lot: created only by reconciliation (F046) for broker positions not matched to a system lot; `is_foreign=true`, no provider position; never sold by signal flows (only manual exit F047).
- Queries: `open_lot_for_position(pp_id)`, `lots_by_underlying(underlying)` (opposite-direction warning), `open_lots_summary()` (count, total cost basis — risk limits), `lock_lot(lot_id)` (`FOR UPDATE`).

## Out of scope
Fill intake (F040), reconciliation (F046).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ledger/lots.py` | pure `apply_fill`, `LotChange` | SQL |
| `backend/app/ledger/lots_repo.py` | queries + `lot_events` insert | decisions |
| `backend/tests/unit/ledger/test_lots.py` (incl. hypothesis: qty never negative, avg stays within [min,max] fill prices) | tests | — |
| `backend/tests/integration/ledger/test_lots_repo.py` | partial unique index behaviour (re-entry = new lot) | — |

## Tests
- Add 2@1.00 then 2@2.00 → qty 4, avg 1.50.
- Sell all → closed; later buy for same provider position → new lot.
- Two providers on same contract → two lots.

## Acceptance criteria
FR-16 lot rules proven; every change writes `lot_events`.

## Implementation steps
1. Pure `apply_fill`. 2. Repo. 3. Tests.
