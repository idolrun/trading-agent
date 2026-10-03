# F075 — A person's resolution of an ambiguous card persists

## Before (audit)
Choosing a position on an ambiguous review card only set `card.provider_position_id` (marked with an inline `ponytail:` note). No link row and no provider-ledger change were written, so the next follow-up still saw the old state, for example `remaining_fraction` not halved.

## What changed
`ApprovalService._edit` (`cards/approval_service.py`):
- The chosen position must be a candidate of the card and in its lane (F068).
- On a link review card (`missing_fields` has `chosen_position_id`), the edit turns the proposal into a re-link proposal: action `review`, `relink_signal_id = card.signal_id`, candidates = the chosen position.
- Approving it runs the existing, tested `RelinkService.apply_approved` path through the approval graph (`execute → _relink`). That writes a `user_correction` `message_links` row superseding the ambiguous one, the provider-ledger change, and a fresh card for the action when the user holds a lot. That card is approved separately and revalidated as usual.
- Approving the resolution places no order.

## Tests
`tests/integration/graph/test_ambiguity_resolution.py`:
- "trim half" with SPY + QQQ → review;
- choose QQQ → approve → link `user_correction` → QQQ trimmed to 0.5, SPY untouched, no order;
- a later "out" replying to the trim resolves to QQQ through the persisted link (`reply`) and closes it.
