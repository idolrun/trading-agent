# F037 — Out-of-Order Arrival & Re-linking

## Objective
When an event arrives after a later-posted event from the same provider was already applied, apply it (flagged `late`) and re-check that provider's later follow-ups that were linked to nothing or were ambiguous; any changed result becomes a review card — never an automatic change.

## Requirements covered
- PRD FR-1b, C89, G5 (no silent feed-only), AC-18 ("A3 is never processed against a provider book that does not yet contain A1").
- TRD §2.4 out-of-order handling, §12 row "A3 delivered before A1".

## Dependencies
- Depends on: F035
- Produces: `RelinkService.recheck_after_late(provider_id, late_posted_at)`, system review cards `action='review'`, `dedupe_key='relink:{signal_id}:{position_id}'`.
- Used by: F047 (same relink-approval path for user corrections), F063.

## Scope
1. F012 sets `late=true`; the message graph applies the late event normally (label `late`).
2. After its `apply` commits, `recheck_after_late`: find this provider's signals with `posted_at > late_posted_at` whose **current** link outcome is `none` or `ambiguous` and whose type is a follow-up.
3. For each: rebuild context as of now and re-run interpretation + correlation only (graph invoked in `recheck` mode: `load_context → candidates → jev/llm → correlation`, no `apply`).
4. If the new `LinkDecision` differs (e.g. now `linked` to the late entry's position) → create a system review card proposing the link (and the action that would follow); approval (F036) writes a new `message_links` row (`method='user_correction'`, `supersedes_link_id`) and applies the ledger change, then normal decide/card flow for the user's lot (exit card etc.).
5. Unchanged → nothing. Count late events (metric; the ≤ 300 ms reorder buffer is **not** built unless counts justify it — TRD §2.4).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ledger/relink.py` | `RelinkService` | automatic ledger mutation |
| `backend/tests/graph/test_out_of_order.py` | tests | — |

## Files to modify
`app/graph/message_graph.py` (recheck mode entry + post-commit hook), `app/cards/approval_service.py` (relink approval handler).

## Tests
- A3 ("take half") delivered and applied before A1 (entry) → A3 `none` + (if user had no lot) feed; A1 arrives late → review card "link A3 to GOOGL position?"; approving creates link + provider trim.
- Recheck with unchanged result → no card.
- Never automatic: without approval, `message_links` has no new row.

## Acceptance criteria
TRD §12 row "A3 delivered before A1" passes: no silent feed-only.

## Implementation steps
1. Recheck query. 2. Graph recheck mode. 3. Review card creation. 4. Approval handler. 5. Tests.
