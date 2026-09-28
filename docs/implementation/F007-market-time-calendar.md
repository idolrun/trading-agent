# F007 — Market Time & Exchange Calendar

## Objective
Pure, well-tested time utilities: the ET exchange clock, NYSE sessions (holidays, half days), Discord snowflake → post time, and relative-expiry resolution. Everything else asks this module about time.

## Requirements covered
- Decision log: C4, C5, C53.
- PRD §12 Time (internal clock ET + exchange calendar; display local), FR-15 (relative dates resolved by code), §6 message post time from Discord ID, 7.7 (regular hours), 7.6 (session close for cutoffs), FR-4 (market hours).
- TRD §3 (`zoneinfo` + `exchange_calendars` XNYS).

## Dependencies
- Depends on: F001
- Produces: `app/markettime.py` API below.
- Used by: F011, F012, F013, F018, F025, F027, F039, F045.

## Scope — public API (all pure except `now_utc`, injectable clock)
```python
ET = ZoneInfo("America/New_York")
DISCORD_EPOCH_MS = 1420070400000
class Clock(Protocol): def now(self) -> datetime   # tz-aware UTC; SystemClock + FixedClock for tests
def snowflake_to_datetime(snowflake: int) -> datetime           # UTC, ms precision
def to_et(ts: datetime) -> datetime
def session_for(day: date) -> Session | None                    # open/close (UTC), is_half_day
def is_regular_hours(ts: datetime) -> bool
def next_regular_open(ts: datetime) -> datetime                 # 9:30 ET next session (7.7)
def session_close(day: date) -> datetime
def resolve_expiry_token(token: str, now: datetime) -> date | None
```
Expiry tokens handled: `0dte`, `today`, `1dte`, `Ndte` (N sessions ahead), weekday names (`fri`, `friday`: the next occurrence **including today**), `m/d`, `m/d/yy`, `mon d` (`oct 16`). Unrecognised → `None` (caller routes to review). Validity of the date as a listed expiry is checked against the chain in F019, not here.

## Out of scope
Chain-listed expiry validation (F019); UI local-time display (frontend).

## Ambiguities (recorded, not silently chosen)
- **OD-22:** `Ndte` — N calendar days vs N trading sessions. Proposal: **trading sessions** (1DTE posted Friday → Monday). Needs confirmation.
- Weekday name on the same weekday (e.g. "friday" posted Friday): proposal = today (0DTE), consistent with 45% 0DTE workload (§3). Needs confirmation (part of OD-22).
- `m/d` without year: nearest future date (year rollover in Dec).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/markettime.py` | functions above; wraps `exchange_calendars.get_calendar("XNYS")` loaded once | DB, config reads |
| `backend/tests/unit/test_markettime.py` | table-driven + hypothesis tests | — |

## Files to modify
`backend/pyproject.toml` — `uv add exchange-calendars`.

## Tests (unit)
- Snowflake known vector (a documented Discord ID → timestamp).
- Holidays (e.g. 2026-11-26 Thanksgiving closed), half day (2026-11-27 close 13:00 ET), DST transitions.
- `is_regular_hours` boundaries 09:29:59 / 09:30:00 / 15:59:59 / 16:00:00 ET.
- `resolve_expiry_token` table incl. year rollover, Friday-on-Friday, 1DTE over weekend/holiday.
- Property: `next_regular_open(ts) > ts` and lands on a session open.

## Acceptance criteria
All functions timezone-aware (reject naive datetimes with `ValueError`); tests pass; no function reads the server's local timezone.

## Implementation steps
1. Add dependency. 2. Clock protocol + snowflake conversion. 3. Session helpers over XNYS. 4. Expiry token parser. 5. Tests.

## Open decisions touched
OD-22.
