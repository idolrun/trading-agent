"""Exchange clock: ET time, NYSE (XNYS) sessions, Discord snowflake times, relative expiries.

Pure functions over timezone-aware datetimes (naive input -> ValueError). The only clock is the
injectable `Clock`. Nothing here reads the server's local timezone.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Protocol
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

ET = ZoneInfo("America/New_York")
DISCORD_EPOCH_MS = 1420070400000
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock: returns a set instant until moved."""

    def __init__(self, at: datetime) -> None:
        self.at = _aware(at)

    def now(self) -> datetime:
        return self.at

    def advance(self, delta: timedelta) -> None:
        self.at += delta


class ControlledClock:
    """`environment=test` only (E2E, F062): the system clock until set, then frozen where the
    test control plane puts it (app/api/routes/testing.py)."""

    def __init__(self) -> None:
        self.at: datetime | None = None

    def now(self) -> datetime:
        return self.at or datetime.now(UTC)

    def set(self, at: datetime) -> None:
        self.at = _aware(at)

    def advance(self, delta: timedelta) -> None:
        self.at = self.now() + delta


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime  # UTC
    close: datetime  # UTC
    is_half_day: bool


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {ts!r}")
    return ts


@cache
def _calendar() -> xcals.ExchangeCalendar:
    """XNYS loaded once. Covers roughly 20 years back to 1 year ahead of process start."""
    return xcals.get_calendar("XNYS")


def _in_bounds(day: date) -> date:
    cal = _calendar()
    if not cal.first_session.date() <= day <= cal.last_session.date():
        raise ValueError(f"{day} is outside the loaded XNYS calendar")
    return day


def snowflake_to_datetime(snowflake: int) -> datetime:
    """Post time encoded in a Discord ID (UTC, ms precision)."""
    if snowflake <= 0:
        raise ValueError(f"invalid snowflake: {snowflake}")
    return _UNIX_EPOCH + timedelta(milliseconds=(snowflake >> 22) + DISCORD_EPOCH_MS)


def to_et(ts: datetime) -> datetime:
    return _aware(ts).astimezone(ET)


def session_for(day: date) -> Session | None:
    """The regular session on `day` (an ET calendar date), or None if the exchange is closed."""
    cal = _calendar()
    if not cal.is_session(_in_bounds(day)):
        return None
    open_: datetime = cal.session_open(day).to_pydatetime()
    close: datetime = cal.session_close(day).to_pydatetime()
    return Session(day=day, open=open_, close=close, is_half_day=close.astimezone(ET).hour < 16)


def session_close(day: date) -> datetime:
    session = session_for(day)
    if session is None:
        raise ValueError(f"{day} is not a trading session")
    return session.close


def is_regular_hours(ts: datetime) -> bool:
    session = session_for(to_et(ts).date())
    return session is not None and session.open <= ts < session.close


def sessions_ahead(day: date, n: int) -> date:
    """First session on/after `day` (n=0), or the n-th session after that one."""
    if n < 0:
        raise ValueError("n must be >= 0")
    cal = _calendar()
    label = cal.date_to_session(_in_bounds(day), direction="next")
    for _ in range(n):
        label = cal.next_session(label)
    result: date = label.date()
    return result


def next_regular_open(ts: datetime) -> datetime:
    """The next 9:30 ET open strictly after `ts` (7.7 off-hours release)."""
    day = to_et(ts).date()
    session = session_for(day)
    if session is not None and ts < session.open:
        return session.open
    next_day = sessions_ahead(day + timedelta(days=1), 0)
    next_session = session_for(next_day)
    assert next_session is not None  # noqa: S101 — sessions_ahead returns a session
    return next_session.open


# --- Relative expiries (FR-15) ---

_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
}  # fmt: skip
_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}  # fmt: skip
_DTE = re.compile(r"(\d{1,3})\s*dte")
_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?")
_MONTH_DAY = re.compile(r"([a-z]+)\.?\s*(\d{1,2})")


def resolve_expiry_token(token: str, now: datetime) -> date | None:
    """Resolve a relative/short expiry against the ET date of `now`. Unrecognised -> None.

    OD-22 proposals (pending confirmation): `Ndte` counts trading sessions; a weekday name
    means its next occurrence including today. Whether the date is a listed expiry is
    checked against the chain later (F019), not here.
    """
    text = token.strip().lower()
    today = to_et(now).date()
    if text in ("0dte", "today"):
        return sessions_ahead(today, 0)
    if m := _DTE.fullmatch(text):
        return sessions_ahead(today, int(m.group(1)))
    if text in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[text] - today.weekday()) % 7)
    if m := _SLASH.fullmatch(text):
        month, day, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year is not None:
            full_year = int(year) + 2000 if len(year) == 2 else int(year)
            return _date_or_none(full_year, month, day)
        return _nearest_future(today, month, day)
    if (m := _MONTH_DAY.fullmatch(text)) and m.group(1) in _MONTHS:
        return _nearest_future(today, _MONTHS[m.group(1)], int(m.group(2)))
    return None


def _date_or_none(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _nearest_future(today: date, month: int, day: int) -> date | None:
    """This year's date if not in the past, else next year's (Dec -> Jan rollover)."""
    candidate = _date_or_none(today.year, month, day)
    if candidate is not None and candidate >= today:
        return candidate
    return _date_or_none(today.year + 1, month, day)
