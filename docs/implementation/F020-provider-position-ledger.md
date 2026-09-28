# F020 — Provider Position Ledger

## Objective
Track every provider's called positions (entry → trims/stops/TPs → exit), whether or not the user traded, with an audited history row for every change.

## Requirements covered
- Decision log: C20 (multi-day/multi-week holds followed until the provider exits).
- PRD FR-16 (maintain provider-position ledger), C72 (two ledgers), §6 (owned by exactly one provider; scope per author — OD-5 resolved), FR-1c (stale entries still update the provider ledger), C62 (adds), C73 (corrections — write path used by F047), §14 (provider positions and their history).
- TRD §6 provider position state machine `open → trimmed → closed (+ corrected)`, OD-13 (history rows).

## Dependencies
- Depends on: F005
- Produces: pure `apply_to_position(position | None, signal, link) -> PositionChange`; repo `lock_open_book(provider_id)`, `insert_position`, `update_position`, `insert_event`.
- Used by: F021, F022, F026, F037, F047.

## Scope — transitions (pure)
| Signal (linked) | Effect on provider position | Event `change` |
|---|---|---|
| `entry` / `stock_entry`, no matching open position | create `open`, `provider_entry_price`, stop/TPs if stated | `open` |
| `entry` on the same contract as an open position, or `add` | unchanged status; record add price | `add` |
| `trim` with fraction | `remaining_fraction *= (1 − f)`; status `trimmed` | `trim` |
| `trim` without fraction | status `trimmed`; fraction unknown (unchanged) | `trim` |
| `full_exit` / `stock_exit` | `closed`, `closed_at` | `exit` |
| `soft_exit` | no change (provider still holding, C52) | — |
| `stop_set` / `stop_move` / `stop_cancel` (`stop_removed`) | `stop_level`, `stop_basis` updated / cleared | `stop_*` |
| `tp_set` | `tp_levels` replaced | `tp_set` |
| `tp_hit_notice` | no change (OD-18 proposal: feed only) | — |
| manual correction (F047) | any field; `is_corrected=true` | `correct` |
Unknown/illegal transitions (e.g. trim on closed) → `InvalidTransition` → review card (never silently ignored).

## Out of scope
Linking (F021), user lots (F022), cards (F025).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ledger/positions.py` | pure transition function + `PositionChange` | SQL |
| `backend/app/ledger/repo.py` | position/event queries; `lock_open_book` = `SELECT … WHERE provider_id=:p AND status<>'closed' FOR UPDATE` | decisions |
| `backend/tests/unit/ledger/test_positions.py`, `backend/tests/integration/ledger/test_position_repo.py` | tests | — |

## Concurrency
Only called inside the provider slot (F012) and inside T3/T7 with `FOR UPDATE` on the book (schema doc §8).

## Tests
- Unit: each row of the table; trim half twice → remaining 0.25; illegal transitions raise.
- Integration: event row per change with before/after; per-author ownership (FK composite from F005 prevents cross-provider link).

## Acceptance criteria
Every change has exactly one `provider_position_events` row; stale entries can be applied (no staleness check here).

## Implementation steps
1. `PositionChange` + pure transitions. 2. Repo with locking. 3. Tests.

## Open decisions touched
OD-13, OD-18.
