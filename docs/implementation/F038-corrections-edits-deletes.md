# F038 — Corrections, Edits & Deletes

## Objective
Handle provider corrections (new message or edit) and deletions: before the user trades, update via a fresh card that needs re-approval; after the user traded, alert only; on deletion of the entry message, invalidate the pending card and alert. Numbers are never auto-corrected.

## Requirements covered
- PRD 7.5, C16, C48, C78, AC-5, FR-5 (`correction` type), §21 (without edit events, edits are lost — OD-2).
- TRD §2.1 (supersession resume), Appendix A `is_correction`, `correction_target`.

## Dependencies
- Depends on: F036
- Produces: `CorrectionService.handle(event_or_signal)`.
- Used by: F062.

## Scope
Target resolution: an `edit` event targets its own `message_id`; a `correction` signal targets `correction_target_message_id` (must be the same provider's message within the C71 window; otherwise review card).
| Target state | Behaviour |
|---|---|
| target has a `pending` card (no order yet) | invalidate it (`reason=correction`), resume approval graph `invalidate`; the corrected interpretation produces a **new** card (new event → new `signal_id`) requiring fresh approval; provider position fields updated via `correct` event (source = the correcting signal) |
| user already traded (card `submitted` / lot exists) | **alert only** (`late_correction`): both messages, live P&L of the lot, text "follow-ups now refer to the corrected contract"; provider position updated to the corrected contract; **no order/card proposed**; the lot keeps its own `broker_symbol`, so later exit cards still sell what the user actually holds |
| target has no card and no lot | provider position corrected; feed shows correction |
| `delete` of an entry message with a pending card | invalidate (`reason=deleted`) + alert `message_deleted`; provider ledger unchanged (PRD does not say to close it — flagged in alert) |
| `delete` after trade | alert only |
- Corrected numbers come only from the provider's new text (literal spans); the system never edits a number itself (C78).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/cards/corrections.py` | `CorrectionService` | order submission |
| `backend/tests/scenarios/test_corrections.py` | AC-5 scenarios | — |

## Files to modify
`app/graph/message_graph.py` (route `edit`/`delete` events and `correction` signals to the service inside `apply`), `app/pipeline/apply.py` (correction branch in T3).

## Tests
- Edit before trade: card invalidated, new card pending with corrected strike, re-approval required (AC-5).
- Correction message after fill: alert with both texts + P&L; no card; next "take half" produces exit card for the lot's original contract.
- Delete with pending card: invalidated + alert.

## Acceptance criteria
AC-5 proven end to end with fakes.

## Implementation steps
1. Target resolution. 2. Branches in apply tx. 3. Alert content. 4. Tests.

## Open decisions touched
OD-2 (edit events), OD-24 (edit sequencing time).
