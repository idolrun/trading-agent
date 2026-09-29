# F047 — Manual Operations, Kill Switch & Provider Corrections

## Objective
Let the user act directly — manual exit, manual stop add/move/cancel, manual entry (step-up), kill switch, flatten (step-up), provider-position and link corrections, lot reconcile-adjust — with every manual order passing exactly the same checks as signal orders and every correction audited.

## Requirements covered
- PRD 7.9, UC10, FR-27, C40, C42 (manual orders pass every check), C73 (provider-position corrections audited and fed into evaluation), C77 (flatten scope chosen at trigger), FR-20(2) (manual exits included), AC-3 ("stop firing after a manual exit"), AC-10 (step-up for manual entry and flatten).
- TRD §5.3 (store writes only from user-approved sources, audited), §5.5 (manual orders never pass through the agent).

## Dependencies
- Depends on: F009, F028, F036, F037, F042, F043, F046
- Produces: `ManualService`, endpoints below.
- Used by: F059, F062.

## Design choice
Manual actions reuse the card + approval path: the endpoint creates a `source='manual'` card and, in the same T4 transaction, its `approvals` row (the user's explicit action is the approval; `step_up=true` where required), then starts the approval graph which immediately resumes with `approve`. One path → identical revalidation, invariants and audit (C42, AC-2).

## API contract
| Method | Path | Request | Auth | Behaviour | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/manual/exit` | `{lotId, qty, idempotencyKey}` | session | exit via F043 policy; foreign lots allowed only here (C47) | 404, 422 qty > lot |
| POST | `/api/v1/manual/stops` | `{lotId, op: "add"|"move"|"cancel", level?, basis?, idempotencyKey}` | session | F042 stop policy | 422 |
| POST | `/api/v1/manual/entry` | `{instrument: {instrument, underlying, expiry?, strike?, right?}, qty, limitPrice, idempotencyKey}` | session + **step-up** | F019 identity + tick check, then the card's approval revalidation runs the F024 gates and F041 policy | 403 `step_up_required`, 422 validation/gates (message = reason) |
| POST | `/api/v1/kill-switch` | `{active: bool, reason}` | session | `FlagService.set('kill_switch')`; pauses entry cards and entries (OD-27) | — |
| POST | `/api/v1/flatten` | `{scope: "options"|"stocks"|"all", idempotencyKey}` | session + **step-up** | activates kill switch, then exits every **tracked** lot in scope via F043 (foreign excluded — **OD-31** proposal, C47 read-only) | 403 |
| POST | `/api/v1/positions` | `{providerId, instrument, underlying, expiry?, strike?, right?, entryPrice?, reason}` | session | add provider position (C73) | 422 |
| PATCH | `/api/v1/positions/{id}` | `{fields…, status?, reason}` | session | edit/close; `is_corrected=true` | 404, 422 |
| POST | `/api/v1/links` | `{signalId, providerPositionId, reason}` | session | re-link (new `message_links` row `user_correction`) + ledger update; same-provider enforced by FK | 422 cross-provider |
| POST | `/api/v1/lots/{id}/adjust` | `{qty, reason}` | session + **step-up** | `reconcile_adjust` to broker qty (F046 resolution) | 422 |
| PUT | `/api/v1/providers/{providerId}/profile` | `{aliases?, lexicon?, habits?, reason}` | session | writes LangGraph store namespace `("provider", id)`; audited in `manual_corrections(target_type='provider_profile')` | 422 |
Every correction inserts `manual_corrections` and an `eval_labels` row (`source='provider_correction'`) when it changes an interpretation outcome tied to a message (C73): a link correction, or an edit of a position a signal opened. Label shape: `{signals: [{idx, msg_type, corrected?}], link: {idx, provider_position_id} | null}`; `split = split_for(ET trading day)`. A refused manual order (422) leaves its invalidated card as audit.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/manual/service.py` | `ManualService`: exit / stops / entry / kill switch / flatten / lot adjust, all through a manual card | new order paths (reuse executor) |
| `backend/app/manual/corrections.py` | `Corrections`: position / link / profile corrections (T7), audit + eval labels | — |
| `backend/app/evalkit/split.py` | `split_for(day)`: deterministic dev/test split (F048 reuses it) | — |
| `backend/app/api/routes/manual.py`, `backend/app/api/schemas/manual.py` | thin routes | — |
| `backend/tests/integration/api/test_manual_api.py`, `backend/tests/scenarios/test_manual_ops.py`, `backend/tests/unit/evalkit/test_split.py` | tests | — |

## Files to modify
`app/main.py` (services, `app.state.store`, router), `app/decide/types.py` (`ProposedAction.contract`: a manual entry names its own contract, no provider position does), `app/cards/approval_service.py` (`_Loaded.target()` resolves foreign-lot exits and manual entries; `decide(stepped_up=)` records the flatten step-up), `app/execution/fills.py` + `app/instruments.py` (`option_from_occ`: a manual entry's first fill opens a lot from the order's own contract; it has no provider position, so it is foreign-shaped by the `foreign_iff_no_position` CHECK), `app/ledger/relink.py` (`apply_approved(provider_id, position_id, signal_id, audit=)`: a manual re-link shares its transaction and audit). No migration: idempotency is the card `dedupe_key` (`manual:{idempotencyKey}`, flatten `manual:{key}:{lotId}`), and `card_id = uuid5(dedupe_key)` (F026).

## Security
Step-up on manual entry, flatten, lot adjust; idempotency keys prevent double submission from double taps; manual entry never touches a model.

## Tests
- Manual exit then broker stop fires → stop had been reduced; no oversell (AC-3).
- Manual entry without step-up → 403; with step-up and a risk limit breached → 422 with reason (C42).
- Flatten options → kill switch on, every tracked option lot exited, foreign untouched.
- Correction writes `manual_corrections` + `provider_position_events` + eval label.
- Cross-provider link attempt → 422 (FK).

## Acceptance criteria
FR-27 complete; C42 identical checks proven; AC-10 step-up proven at API level.

## Implementation steps
1. Manual card + approval creation. 2. Exit/stops/entry endpoints. 3. Kill switch + flatten. 4. Corrections + store writes. 5. Lot adjust. 6. Tests.

## Open decisions touched
OD-27, OD-31.
