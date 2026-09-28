# F036 — Approval Graph per Card & Decision API

## Objective
Human-in-the-loop for every card: a small LangGraph thread per card (`card:<card_id>`) that pauses in `interrupt()` until the user approves/edits/rejects, the card expires, it is invalidated, or the process restarts — then revalidates (C22) and submits through `submit_order()`. Approval records are written **before** resuming.

## Requirements covered
- Decision log: C14.
- PRD G3, §9 (every entry/add/exit/trim/stop/bracket needs approval), 7.1 step 5 (one click; inferred cards too; review cards need missing fields first), 7.2 (C22 invalidation → no order), 7.3 step 4, C18, C32, AC-2, AC-4, AC-7, AC-14 (suggested underlying needs explicit confirmation), AC-15 (blank qty not approvable).
- TRD §5.1 resume table, §5.6 idempotency on resume, §9 (open cards expire on restart). OD-11 resolved (graph per card).

## Dependencies
- Depends on: F035, F027, F028
- Produces: `build_approval_graph()`, `ApprovalService.decide(card_id, request, auth)`, `start_approval(card_id)`, `resume(card_id, value)`, timer handler `card_expiry`, `POST /api/v1/cards/{id}/decision`, minimal `CardExecutor` (per-action execution entry point, extended by F041–F042).
- Used by: F038, F041–F047, F055.

## Graph
```
START → await_approval ──interrupt({card_id})──▶ (resume value)
          ├─ approve    → revalidate ─ ok → execute (CardExecutor) → END
          │                        └─ fail → mark invalidated (revalidate_failed) → END
          └─ reject | expire | invalidate → END   (card state already set by the resumer)
```
- **State:** `card_id`, `decision`, `approval_id`, `revalidation`, `order_ids`.
- `await_approval` contains **nothing before `interrupt()`** (node re-runs from its start on resume).
- **Resume values** (TRD §5.1): `{"decision":"approve","approval_id"}`, `{"decision":"reject"}`, `{"decision":"expire"}`, `{"decision":"expire","reason":"restart"}`, `{"decision":"invalidate","by": message_id}`. Resumed with `Command(resume=value)` on `thread_id=f"card:{card_id}"`.
- `revalidate`: entries/adds — fresh quote, C22 (time from `posted_at`, ask band), F024 gates again (limits/kill switch may have changed); exits — position still open, lot qty; review cards — every missing field supplied.
- `execute`: `CardExecutor.execute(card, approved_proposal, approval_id)` → builds `OrderIntent`s → `submit_order()`; card → `submitted` after the intent commits. Minimal executor here: entry = one limit order; exit/trim/close = one limit at mid; stop = broker stop; TP = limit (F041–F042 replace with full policies).
- Idempotency: re-executed `execute` recomputes the same `client_order_id` → existing order returned (AC-7).

## Decision API
| Method | Path | Request | Response | Auth | Service | Errors |
|---|---|---|---|---|---|---|
| POST | `/api/v1/cards/{cardId}/decision` | `{decision: "approve"|"reject"|"edit", version: int, edits?: {qty?, price?, tpSplit?, breakevenChoice?: "user_avg"|"provider_entry", confirmedUnderlying?, chosenPositionId?, expiry?}}` | `CardDetail` | session; **step-up** if `card.source='manual'` and action entry (F047) | `ApprovalService.decide` | 404; 409 `conflict` (not pending / version mismatch / already decided); 422 `invalid` (qty missing/≤0/> lot, missing review fields, suggestion not confirmed) |
- `edit` → validates + applies edits, re-runs validation/decide for changed identity fields (e.g. confirmed underlying), bumps `version`, card stays `pending` (user then approves the new version).
- `approve` → may carry only `qty`, `tpSplit`, `breakevenChoice` edits; T4: lock card, check `pending` + `version`, validate edits (qty rules: 1 ≤ qty; exits ≤ lot qty; blank qty rejected — AC-15), insert `approvals` (`approved_proposal`, `session_id`, `step_up`), transition `approved`, commit → `resume`.
- `reject` → approvals row (`reject`) + transition `rejected` → resume.
- Expiry timer handler → transition `expired` (ignore `IllegalTransition`) → resume `expire`.
- Supersession hook (from F026 post-commit) → resume `invalidate` for already-invalidated cards.
- `start_approval(card_id)` is called from the F035 `notify` hook for every new `pending` card (disabled cards never start a thread).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/graph/approval_graph.py` | graph, nodes (adapters) | business rules |
| `backend/app/cards/approval_service.py` | `decide`, T4, edit handling, resume calls | HTTP |
| `backend/app/execution/card_executor.py` | `CardExecutor` minimal policies | invariants (F028) |
| `backend/app/api/routes/card_decisions.py`, `backend/app/api/schemas/card_decisions.py` | thin route + schemas | rules |
| `backend/tests/graph/test_approval_graph.py`, `backend/tests/api/test_card_decision_api.py`, `backend/tests/scenarios/test_card_invalidation.py` | tests | — |

## Files to modify
`app/graph/message_graph.py` (notify hook), `app/timers/service.py` (register `card_expiry`), `app/pipeline/hooks.py`.

## Tests
- Interrupt/resume for every resume value (TRD §12 graph tests).
- AC-4: entry card invalidated by (a) timeout, (b) ask above band at revalidate, (c) superseding stop message — no order in each case.
- Approve twice concurrently → one approval, one order; stale `version` → 409.
- Blank-qty trim approve → 422 (AC-15); suggested-underlying review approve without `confirmedUnderlying` edit → 422 (AC-14).
- Crash inside `execute` after intent commit, resume → no duplicate order (AC-7).
- AC-2: every order in these tests has an approval row.

## Acceptance criteria
No order without an approval record (except §9 auto actions); every resume path proven; approvals written before resume.

## Implementation steps
1. Approval graph + checkpointer thread naming. 2. `ApprovalService` (T4, edits, resume). 3. Minimal `CardExecutor`. 4. Expiry timer handler + supersession resume hook. 5. Decision route. 6. Tests.
