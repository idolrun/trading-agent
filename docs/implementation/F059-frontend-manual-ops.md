# F059 — Manual Operations & Kill Switch UI

## Objective
UI for manual exit, manual stop add/move/cancel, manual entry (step-up), kill switch, flatten with scope (step-up), provider-position corrections, re-linking, and lot reconcile-adjust.

## Requirements covered
- PRD 7.9, FR-27, UC10, C40, C73, C77, AC-10 (step-up in UI).

## Dependencies
- Depends on: F057, F053 (backend F047)
- Produces: action buttons/forms on lot, position and card-detail screens; header kill-switch control; `/manual/entry` form.
- Used by: F062.

## Components
| Where | Control | Endpoint | Step-up |
|---|---|---|---|
| Lot detail | Exit (qty), Stop add/move/cancel | `/manual/exit`, `/manual/stops` | no |
| Lot detail (mismatch) | Adjust to broker qty | `/lots/{id}/adjust` | yes |
| Header | Kill switch toggle (confirm dialog with reason) | `/kill-switch` | no |
| Header menu | Flatten → scope picker (options / stocks / all) → confirm | `/flatten` | yes |
| `/manual/entry` | contract + qty + limit form; shows server validation/gate errors | `/manual/entry` | yes |
| Position detail | Add / edit / close position; re-link signal (candidate list) | `/positions`, `/links` | no |
| Provider page | Aliases/lexicon editor | `/providers/{id}/profile` | no |
All mutations send an `idempotencyKey` (UUID generated per dialog open) so double taps cannot double-submit.

## States
Pending buttons disabled; server errors shown inline; success toasts; confirmation dialogs for destructive actions (flatten, kill switch).

## Accessibility
Dialogs trap focus, labelled; destructive actions visually distinct and require explicit confirm.

## Files to create
`frontend/features/manual/{api.ts,exit-form.tsx,stop-form.tsx,entry-form.tsx,kill-switch.tsx,flatten-dialog.tsx,adjust-lot.tsx}`, `frontend/features/positions/{correction-form.tsx,relink-dialog.tsx}`, `frontend/features/providers/profile-editor.tsx`, `frontend/app/(desk)/manual/entry/page.tsx`, tests.

## Tests
Step-up requested before manual entry/flatten/adjust; idempotency key stable across retries of one dialog; foreign lot shows exit only.

## Acceptance criteria
Every FR-27 operation reachable; step-up enforced in UI and API.

## Implementation steps
1. API module. 2. Lot actions. 3. Kill switch + flatten. 4. Manual entry. 5. Corrections/relink/profile. 6. Tests.
