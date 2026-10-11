"""Reporting windows (F083). Pure: no clock reads, no I/O.

Boundaries are ET calendar days (the domain clock, markettime.ET), start-inclusive and
end-exclusive. Presets are calendar windows ending today, not rolling 24h spans:

  1d   today so far
  3d / 7d / 30d   the last N ET calendar days including today
  all  everything on record (the start is resolved from the data by the caller)
  custom  startDate .. endDate, both ET dates and both included by the user
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from app.api.errors import ApiError
from app.markettime import ET, to_et

MAX_CUSTOM_DAYS = 1830  # ~5 years


class Period(StrEnum):
    D1 = "1d"
    D3 = "3d"
    D7 = "7d"
    D30 = "30d"
    ALL = "all"
    CUSTOM = "custom"


class Granularity(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


_PRESET_DAYS = {Period.D1: 1, Period.D3: 3, Period.D7: 7, Period.D30: 30}


@dataclass(frozen=True)
class Window:
    """`start` None only for `all` before the caller resolves the first event."""

    period: Period
    start: datetime | None
    end: datetime
    label: str

    def resolved(self, first_event: datetime | None) -> "Window":
        if self.start is not None:
            return self
        start = et_midnight(to_et(first_event).date()) if first_event else self.end
        return Window(self.period, start, self.end, self.label)

    @property
    def previous(self) -> "Window | None":
        """The equally long window right before this one. None for `all`."""
        if self.start is None or self.period is Period.ALL:
            return None
        # shift both ends back by the window's whole ET days: equal length, same time of day
        days = max(1, math.ceil((self.end - self.start) / timedelta(days=1)))
        start = et_midnight(to_et(self.start).date() - timedelta(days=days))
        return Window(self.period, start, self.end - (self.start - start), "previous")

    @property
    def granularity(self) -> Granularity:
        if self.start is None:
            return Granularity.MONTH
        days = (self.end - self.start) / timedelta(days=1)
        if days <= 1.01:
            return Granularity.HOUR
        if days <= 62:
            return Granularity.DAY
        if days <= 400:
            return Granularity.WEEK
        return Granularity.MONTH


def et_midnight(day: date) -> datetime:
    """00:00 ET of `day` as UTC (DST-safe: built from the local date, not by adding 24h)."""
    return datetime.combine(day, time(0), tzinfo=ET).astimezone(UTC)


def resolve(
    period: Period, start_date: date | None, end_date: date | None, now: datetime
) -> Window:
    today = to_et(now).date()
    if period is Period.CUSTOM:
        if start_date is None or end_date is None:
            raise ApiError("a custom period needs startDate and endDate", "invalid", 422)
        if end_date < start_date:
            raise ApiError("endDate is before startDate", "invalid", 422)
        if (end_date - start_date).days >= MAX_CUSTOM_DAYS:
            raise ApiError(f"a custom period is at most {MAX_CUSTOM_DAYS} days", "invalid", 422)
        end = et_midnight(end_date + timedelta(days=1))
        label = f"{start_date.isoformat()} to {end_date.isoformat()} (ET days, end date included)"
        return Window(period, et_midnight(start_date), end, label)
    if start_date is not None or end_date is not None:
        raise ApiError("startDate and endDate only apply to period=custom", "invalid", 422)
    if period is Period.ALL:
        return Window(period, None, now, "All recorded history")
    days = _PRESET_DAYS[period]
    label = "Today so far (ET)" if days == 1 else f"Last {days} ET calendar days, including today"
    return Window(period, et_midnight(today - timedelta(days=days - 1)), now, label)


def floor_et(ts: datetime, gran: Granularity) -> datetime:
    """Start of the bucket holding `ts`, as a naive ET wall-clock time."""
    local = to_et(ts).replace(tzinfo=None)
    if gran is Granularity.HOUR:
        return local.replace(minute=0, second=0, microsecond=0)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if gran is Granularity.DAY:
        return day
    if gran is Granularity.WEEK:
        return day - timedelta(days=day.weekday())  # Monday, like date_trunc('week')
    return day.replace(day=1)


def bucket_key(naive_et: datetime, gran: Granularity) -> str:
    if gran is Granularity.HOUR:
        return naive_et.strftime("%Y-%m-%dT%H:00")
    if gran is Granularity.MONTH:
        return naive_et.strftime("%Y-%m")
    return naive_et.strftime("%Y-%m-%d")


def bucket_keys(start: datetime, end: datetime, gran: Granularity) -> list[str]:
    """Every bucket from the one holding `start` to the one holding `end` (exclusive end)."""
    cur = floor_et(start, gran)
    last = floor_et(end - timedelta(microseconds=1), gran)
    keys: list[str] = []
    while cur <= last:
        keys.append(bucket_key(cur, gran))
        if gran is Granularity.HOUR:
            cur += timedelta(hours=1)
        elif gran is Granularity.DAY:
            cur += timedelta(days=1)
        elif gran is Granularity.WEEK:
            cur += timedelta(days=7)
        else:
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return keys
