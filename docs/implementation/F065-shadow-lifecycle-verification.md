# F065 — Shadow Position Lifecycle Verification

## Objective
Prove that an approved shadow card moves safely through the whole position lifecycle:
card → approval → order intent → simulated order → fill → lot → bracket → trim → exit → closed lot,
with the broker position, the lot and the provider position agreeing after every step. No product code changed.

## Requirements covered
PRD 7.1–7.4, FR-16, FR-20–FR-22, AC-2, AC-3, AC-7, AC-15, AC-16, §11 (reconcile before entries). TRD §2.1, §5.

## Scope
- `backend/tests/integration/graph/test_lifecycle.py`: the ordered lifecycle plus gap cases (duplicate approval, duplicate fill, 1-contract trim, oversized exit, cross-provider exit, reconcile mismatch blocks entries).
- `frontend/e2e/lifecycle.spec.ts`: the same flow through the dashboard (shadow mode).
- Reconciliation is skipped in shadow mode (OD-33 proposal, `reconcile/service.py`): the in-memory `SimulatedBroker` is not the truth there. The lifecycle worlds therefore run in **paper mode over the `SimulatedBroker`** (same code paths, no real broker). OD-33 is unchanged.

## Coverage matrix (what proves what)
| Stage / safety property | Test |
|---|---|
| approval → one order intent, one client id, audit rows | `test_approved_entry_fills_and_creates_the_lot` |
| fill → one fill, one lot, provider-position link, avg price | same |
| bracket: stop = lot, TP ≤ lot, one OCO, approved | `test_bracket_never_exceeds_the_entry_and_shares_one_oco` |
| TP fill resizes stop; stop fill cancels TPs | `graph/test_brackets.py` (existing) |
| trim → exit → closed lot, position closed, no orphan order, no second exit | `test_full_lifecycle_entry_trim_exit_closed` |
| duplicate approval / duplicate fill | `test_duplicate_approval_does_not_duplicate_order`, `test_duplicate_fill_does_not_double_the_lot`, `execution/test_fills.py`, `execution/test_submit_order.py` |
| 1-contract trim needs a quantity (AC-15) | `test_one_contract_trim_requires_a_quantity` |
| exit above the lot refused; FR-20 sell ≤ lot at submit | `test_exit_cannot_exceed_the_lot`, `execution/test_submit_order.py::test_invariants_block_without_a_row`, `execution/test_fills.py` |
| cross-provider exit never reaches another provider's lot | `test_cross_provider_exit_is_blocked` |
| unfilled exit restores protection | `graph/test_exit_execution.py` (existing) |
| crash after submit does not duplicate | `graph/test_restart.py` (existing) |
| reconcile mismatch → alert, entries blocked, no silent repair | `test_reconciliation_blocks_entries_on_a_mismatch`, `reconcile/test_reconcile.py` |

## Observed behaviours worth knowing
- After a filled trim the stop and TP legs are re-sized to the remaining contracts (stop = remaining, TPs ≤ remaining).
- The provider position is `trimmed` after a partial exit and `closed` after the full exit.
- Another provider's exit naming a position it does not own becomes a review card (`link_none`) with no lot.
