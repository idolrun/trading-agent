# F025 — Card State Machine & Validity

## Objective
Own every card state transition (with an audit row each), the C22 validity rules measured from provider post time, supersession detection, and review-card completion rules.

## Requirements covered
- PRD §6 Card types, 7.1 step 5 (review cards need missing fields first), 7.2 (entry card expires on the first of: N s from post time, ask above signal + X%, newer provider message on the same position), 7.3 step 4 (exit card expires only on timeout or when position closed), C18 (unanswered = expire), C22, C90, C75, AC-4.
- TRD §6 card state machine (+ `disabled`, X3), diagram 07 §7.4, OD-14 (supersession trigger).

## Dependencies
- Depends on: F005, F007
- Produces: `CardStateMachine.transition()`, `validity.check_entry(card, quote, now)`, `validity.check_exit(card, position, now)`, `supersedable_cards(position_id, new_msg)`, `apply_review_edits(card, edits) -> CardProposal | errors`, cards repo.
- Used by: F026, F027 (expiry), F036 (decision + revalidate), F038, F041, F045.

## State transitions
| From | To | Trigger | reason_code |
|---|---|---|---|
| — | pending | apply tx (normal/inferred/review) | created |
| — | disabled | apply tx (limit hit, tier too small, config incomplete) | created |
| pending | pending | user edit / review completion (version+1) | edited / review_completed |
| pending | approved | user approve (approval row first) | approved |
| pending | rejected | user reject | rejected |
| pending | expired | timer / restart | timeout / restart |
| pending | invalidated | superseding message, correction, delete, price band | superseded / correction / deleted / price_band |
| approved | invalidated | revalidate fails (C22) | revalidate_failed |
| approved | submitted | `submit_order()` wrote intent | submitted |
All other transitions raise `IllegalTransition` (→ 409 at API).

## Scope
- **Validity (pure):**
  - entry/add: invalid if `now > valid_until` (posted_at + N) or `quote.ask > signal_price × (1 + X)`;
  - exit/stop/tp/close: invalid if `now > valid_until` or position closed (for exits) — never by price.
- **Supersession (OD-14 proposal):** when `apply` (F026) links a new signal of type trim/stop_*/full_exit/correction to position P, every `pending` **entry/add** card on P is invalidated inside the same transaction; the caller resumes their approval graphs after commit (F036).
- **Review completion:** a `review` card becomes approvable only when every `missing_fields` item is provided by the user (e.g. `underlying_confirmation`, `qty`, `chosen_position_id`, `expiry`); completion re-runs validation/decide (F036) — this module only checks completeness and records the edit.
- `lot_too_small` / blank-qty cards: approval requires `qty ≥ 1` and `qty ≤ lot.qty` (checked again by F028).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/cards/state.py` | transition table + `IllegalTransition` | SQL |
| `backend/app/cards/validity.py` | pure validity + supersession predicates | SQL |
| `backend/app/cards/proposal.py` | `CardProposal` Pydantic model (jsonb schema), review-completion check | — |
| `backend/app/cards/repo.py` | insert (`ON CONFLICT (dedupe_key) DO NOTHING RETURNING`), `lock(card_id)`, `transition(...)` writing `card_events`, `pending_on_position` | decisions |
| `backend/tests/unit/cards/test_state.py`, `test_validity.py`, `backend/tests/integration/cards/test_cards_repo.py` | tests | — |

## Concurrency
`lock(card_id)` = `SELECT … FOR UPDATE`; decision, timer and supersession race → exactly one wins, others get `IllegalTransition`.

## Tests
- Every legal transition; illegal ones raise.
- Entry validity: N s boundary measured from `posted_at` not `created_at` (card created 20 s after post with N=30 expires 10 s later).
- Ask above band → invalid; exit card never invalid by price.
- Concurrent approve vs expire → one winner (integration).

## Acceptance criteria
AC-4 building blocks: timeout, price band, supersession each produce `invalidated`/`expired` with reason and no order.

## Implementation steps
1. Transition table. 2. Proposal model. 3. Validity predicates. 4. Repo with locking + events. 5. Tests.

## Open decisions touched
OD-14.

## Implementation notes (2026-09-29)
- No `proposal.py`: the stored proposal **is** F023's `ProposedAction` (`decide/types.py`), which gained `signal_price` (stated price / top of range) for the C22 price band. The approval/review completeness check lives in `validity.approval_blockers(proposal, edits, lot_qty)`.
- `state.check_transition(from, to, reason)` enforces the table above (reasons included); `repo.transition` calls it on a locked row, writes one `card_events` row, and bumps `version` when a new proposal is supplied (edit / review completion).
- `check_entry(proposal, quote, now, chase_pct)` → `TIMEOUT` past `valid_until` (strictly after), `PRICE_BAND` when `ask > signal_price × (1 + X%)`; no quote = not checkable here (caller routes to review, OD-12). `check_exit(proposal, position_closed, now)` → `TIMEOUT`, or `SUPERSEDED` for trim/exit cards on a closed position; never price.
- Supersession set: trim, full_exit, stock_exit, stop_set/move/cancel, correction → invalidates pending entry/add cards (`repo.pending_on_position`, `FOR UPDATE`). Soft exit and tp_set do not supersede.
- `repo.insert` sets `card_id = uuid5(CARD_NAMESPACE, dedupe_key)` and uses `ON CONFLICT DO NOTHING`; a re-run returns False and writes no event.
- Approval blockers: disabled → never; each `missing_fields` item must be in the edits; entry/add/trim/exit need `qty ≥ 1`, sells also `qty ≤ lot`; breakeven stop needs a chosen `stop_level`. F023's ambiguous-link missing field is now `chosen_position_id` (the F036 edit key).
