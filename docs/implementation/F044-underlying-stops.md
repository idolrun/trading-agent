# F044 — System-Held Underlying Stops

## Objective
Watch underlying prices for approved stops defined on the underlying (e.g. "769 stop" on SPY 770C) and, when crossed, fire the C25 exit automatically.

## Requirements covered
- PRD 7.4 (stop on the underlying held by the system; crossing fires the C25 exit automatically; candle-close conditions are information only), C49, FR-23, §9 (system-held underlying stop firing after approval is pre-approved), §21 + TRD §9 known gap (no protection while the process is down).
- TRD §7.3 (real-time SPX index needed).

## Dependencies
- Depends on: F043, F010
- Produces: `UnderlyingStopWatcher` (lifespan task).
- Used by: F046 (restart re-subscribe), F063.

## Scope
- Load `stops WHERE held_by='system' AND status='working'`; subscribe `MarketData.stream(underlyings)`; refresh subscriptions on `NOTIFY positions`/stop changes.
- Trigger rule: calls (and long stock using an underlying stop) fire when `last ≤ level`; puts fire when `last ≥ level`. Use `last` trade price (**OD-32:** bid/ask/last choice; proposal `last`).
- Fire once: `UPDATE stops SET status='fired' WHERE stop_id=:id AND status='working' RETURNING` → `ExitPolicy.execute(lot, lot.qty, auto_reason='underlying_stop')`; alert + push.
- Market data stream gap > N s → warning alert "underlying stops unprotected" (degraded) until restored.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/execution/underlying_stops.py` | watcher | pricing |
| `backend/tests/scenarios/test_underlying_stops.py` | tests with scripted stream | — |

## Tests
- SPY 770C with stop 769: stream 770.2 → 769.0 → exit fired once; further ticks do nothing.
- Put with level above → fires on rise.
- Stream gap → alert.

## Acceptance criteria
Exactly-once firing; exit uses the approved C25 path with `auto_reason`.

## Implementation steps
1. Watcher load + subscribe. 2. Trigger + claim. 3. Gap alert. 4. Tests.

## Open decisions touched
OD-4, OD-32.
