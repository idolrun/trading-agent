# F034 — Advisory Note & Note Guard

## Objective
After a card is shown, write a short plain-language "advisory" note explaining what the provider did or intends — from provider-only context — and hide it if it mentions any ticker, strike, expiry or price that is not in the validated fields or source messages. The note can never change a card.

## Requirements covered
- PRD §4 (advisory note; labeled; cannot change fields, actions, quantities or prices; provider-only context), [CONFIRM] note guard (OD-6), C80, AC-13, FR-8.
- TRD §4.6 (separate single call after the card is shown), §5.5 (`@after_agent` note guard), §4.8 (role `note`, own chain and deadline).

## Dependencies
- Depends on: F016, F025, F031
- Produces: `NoteWriter.write(card_id)` (async, off critical path), `note_guard(note, allowed_values) -> GuardResult`.
- Used by: F035 (`notify` node spawns it), F055 (display).

## Scope
- Input: the card's validated fields (contract, prices, levels as shown to the user) + the source message texts + C71 context — **no lots, balances, P&L** (same scope guard as F032).
- Output: ≤ 280 characters, plain text (structured output `{note: str}`).
- **Guard** (enabled by `config.note_guard_enabled`, default true pending OD-6): run F016 `find_candidates` on the note; every ticker (after symbol map), number (strike/price/level) and expiry token must appear in `allowed_values` = validated fields ∪ values found in source messages. Otherwise `note_hidden=true`, alert `note_hidden` (info), note text still stored for audit.
- Persist: `UPDATE cards SET note, note_hidden` (only these columns; `proposal` untouched) + `NOTIFY cards`.
- Failure/timeout (chain exhausted) → no note; no alert beyond `model_calls` (non-critical).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/note.py` | `NoteWriter`, prompt `note_v1`, guard | card mutation beyond note columns |
| `backend/app/interpret/prompts/note_v1.md` | prompt | — |
| `backend/tests/unit/interpret/test_note_guard.py`, `backend/tests/integration/interpret/test_note_writer.py` | tests | — |

## Tests
- Note "Provider bought SPY 770C at 0.95" with those validated → shown.
- Note mentioning "771C" or "$1.10" not in fields/sources → hidden + alert.
- After note write, `cards.proposal` and `version` unchanged (AC-13).
- Guard disabled via config → note shown regardless (for OD-6 = reject).

## Acceptance criteria
AC-13 proven.

## Implementation steps
1. Guard (pure). 2. Prompt + writer via `run_chain("note")`. 3. Persistence of note columns only. 4. Tests.

## Open decisions touched
OD-6.
