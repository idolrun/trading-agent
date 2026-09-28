# F045 — Expiration Management (cutoff 1 card, cutoff 2 auto-close)

## Objective
For positions expiring today: at cutoff 1 create an urgent close card; if not approved by cutoff 2, close automatically (stops/TPs cancelled first, C25 exit).

## Requirements covered
- PRD 7.6, C19, FR-24, UC7, AC-6, §9 (end-of-day forced close at cutoff 2 is pre-approved), §21 (EOD close is the safety net), §3 (45% 0DTE — core path).
- TRD §16.2 #4 (tested first in paper stage), §12 scenario row "0DTE unanswered at cutoff 1 → cutoff 2".

## Dependencies
- Depends on: F043, F027
- Produces: timer handlers `cutoff_1`, `cutoff_2`; scheduler for today's expiring lots.
- Used by: F046 (startup scheduling), F062.

## Scope
- At session open (and whenever an option lot opens with `expiry == today`): schedule `cutoff_1` and `cutoff_2` at `session_close(today) − cutoff_n_min_before_close` (**OD-21** representation; handles half days). `dedupe_key=cutoff{n}:{lot_id}:{date}`.
- `cutoff_1`: if lot open → system card `action='close'`, `source='system'`, qty = lot qty, labels `urgent`, push urgent; start approval graph (normal approval path).
- `cutoff_2`: if lot still open and no `close`/exit order working → cancel protective orders, `ExitPolicy.execute(lot, lot.qty, auto_reason='eod_forced_close')`, invalidate the cutoff-1 card (`reason=superseded`), alert + push. If an approved exit is already working → no action.
- Missing cutoff config → entries disabled via `missing_for_entries` (config incomplete) — expiring lots must never lack the safety net.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/expiration.py` | scheduling + handlers | — |
| `backend/tests/scenarios/test_expiration.py` | AC-6 | — |

## Files to modify
`app/timers/service.py` (register), `app/execution/consumer.py` (schedule on lot open), `app/runtime_config/schema.py` (cutoffs required in `missing_for_entries`).

## Tests
- AC-6: 0DTE lot, no action → cutoff 1 card created; unanswered → at cutoff 2 stops/TPs cancelled then exit submitted with `auto_reason='eod_forced_close'`.
- Approved close before cutoff 2 → no forced close.
- Half day (13:00 ET close) → cutoffs shift.

## Acceptance criteria
AC-6 proven on fakes.

## Implementation steps
1. Scheduling. 2. Cutoff 1 handler. 3. Cutoff 2 handler. 4. Tests.

## Open decisions touched
OD-21.
