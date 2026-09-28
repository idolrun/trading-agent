# F039 — Off-Hours Hold & Release

## Objective
Messages calling for action outside regular hours update the provider ledger immediately but produce their cards only at 9:30 ET, built with fresh validation; the system never submits outside regular hours.

## Requirements covered
- PRD 7.7, C59, §2 non-goal (no extended hours), FR-1c/C90 interaction.
- TRD §2.2 `timers` (off-hours release).

## Dependencies
- Depends on: F027, F035
- Produces: off-hours branch in `decide`/apply, timer handler `offhours_release`, `pipeline/release.py`.
- Used by: F046 (startup reload), F062.

## Scope
- In `apply`: if `not is_regular_hours(now)` and the decision would create an actionable card → store signals/links/ledger as usual, **no card**, schedule `offhours_release` at `next_regular_open(now)` (`dedupe_key=release:{signal_id}`); feed label `held until open`.
- Release handler (at 9:30 ET): for each held signal in post-time order per provider: re-run validation (fresh quotes) and `decide` against the **current** ledger (a position closed overnight → no card), insert cards via the same card-insert code as `apply`, start approval graphs.
- **Validity/staleness clock for held messages (OD-29, proposal):** origin = release time, not post time — otherwise every held entry would be stale at 9:30. Needs confirmation because it deviates from C90's "post time" rule.
- `submit_order()` refuses outside regular hours for all non-auto orders (defence in depth; broker-held stops/TPs are already at the broker).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/pipeline/release.py` | release handler | — |
| `backend/tests/scenarios/test_off_hours.py` | tests | — |

## Files to modify
`app/pipeline/apply.py` (hold branch), `app/decide/engine.py` (`off_hours` input), `app/execution/submit.py` (regular-hours guard), `app/timers/service.py` (register).

## Tests
- Entry at 20:00 ET → no card, ledger updated, timer at next open; at release → card with fresh validation.
- Entry at 20:00 then exit at 21:00 → at release no entry card (position closed).
- Weekend/holiday → next session open.

## Acceptance criteria
No card or order outside regular hours; held cards built at open with fresh validation.

## Implementation steps
1. Hold branch. 2. Release handler. 3. Submit guard. 4. Tests.

## Open decisions touched
OD-29.
