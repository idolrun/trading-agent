from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.markettime import (
    ET,
    FixedClock,
    is_regular_hours,
    next_regular_open,
    resolve_expiry_token,
    session_close,
    session_for,
    sessions_ahead,
    snowflake_to_datetime,
    to_et,
)


def et(*args: int) -> datetime:
    return datetime(*args, tzinfo=ET)  # type: ignore[misc]


def test_snowflake_known_vector() -> None:
    # Example from the Discord developer docs ("Snowflakes").
    assert snowflake_to_datetime(175928847299117063) == datetime(
        2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC
    )
    with pytest.raises(ValueError):
        snowflake_to_datetime(0)


def test_holiday_and_half_day() -> None:
    assert session_for(date(2026, 11, 26)) is None  # Thanksgiving
    half = session_for(date(2026, 11, 27))
    assert half is not None and half.is_half_day
    assert to_et(half.close) == et(2026, 11, 27, 13, 0)
    full = session_for(date(2026, 9, 28))
    assert full is not None and not full.is_half_day
    assert to_et(session_close(date(2026, 9, 28))) == et(2026, 9, 28, 16, 0)
    assert session_for(date(2026, 10, 3)) is None  # Saturday
    with pytest.raises(ValueError):
        session_close(date(2026, 11, 26))


@pytest.mark.parametrize(
    ("day", "open_utc_hour"),
    [
        (date(2026, 3, 6), 14),  # EST
        (date(2026, 3, 9), 13),  # EDT after spring forward
        (date(2026, 10, 30), 13),  # EDT
        (date(2026, 11, 2), 14),  # EST after fall back
    ],
)
def test_open_is_930_et_across_dst(day: date, open_utc_hour: int) -> None:
    session = session_for(day)
    assert session is not None
    assert session.open == datetime(day.year, day.month, day.day, open_utc_hour, 30, tzinfo=UTC)
    assert to_et(session.open).time().isoformat() == "09:30:00"


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (et(2026, 9, 28, 9, 29, 59), False),
        (et(2026, 9, 28, 9, 30, 0), True),
        (et(2026, 9, 28, 15, 59, 59), True),
        (et(2026, 9, 28, 16, 0, 0), False),
        (et(2026, 11, 27, 13, 0, 0), False),  # half day already closed
        (et(2026, 11, 26, 11, 0, 0), False),  # holiday
    ],
)
def test_regular_hours_boundaries(ts: datetime, expected: bool) -> None:
    assert is_regular_hours(ts) is expected
    assert is_regular_hours(ts.astimezone(UTC)) is expected


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (et(2026, 9, 28, 8, 0), et(2026, 9, 28, 9, 30)),  # before today's open
        (et(2026, 9, 28, 9, 30), et(2026, 9, 29, 9, 30)),  # strictly after
        (et(2026, 10, 2, 17, 0), et(2026, 10, 5, 9, 30)),  # weekend
        (et(2026, 11, 25, 17, 0), et(2026, 11, 27, 9, 30)),  # Thanksgiving
    ],
)
def test_next_regular_open(ts: datetime, expected: datetime) -> None:
    assert next_regular_open(ts) == expected


@settings(max_examples=200, deadline=None)
@given(
    st.datetimes(
        min_value=datetime(2026, 1, 1),
        max_value=datetime(2027, 6, 30),
        timezones=st.just(UTC),
    )
)
def test_next_regular_open_property(ts: datetime) -> None:
    opened = next_regular_open(ts)
    assert opened > ts
    session = session_for(to_et(opened).date())
    assert session is not None and session.open == opened


MON = et(2026, 9, 28, 10, 0)  # Monday


@pytest.mark.parametrize(
    ("token", "now", "expected"),
    [
        ("0dte", MON, date(2026, 9, 28)),
        ("today", MON, date(2026, 9, 28)),
        ("0DTE", et(2026, 10, 3, 10, 0), date(2026, 10, 5)),  # Saturday -> next session
        ("1dte", MON, date(2026, 9, 29)),
        ("5dte", MON, date(2026, 10, 5)),  # N trading sessions (OD-22)
        ("1 DTE", et(2026, 10, 2, 10, 0), date(2026, 10, 5)),  # over the weekend
        ("1dte", et(2026, 11, 25, 10, 0), date(2026, 11, 27)),  # over Thanksgiving
        ("fri", MON, date(2026, 10, 2)),
        ("Friday", et(2026, 10, 2, 10, 0), date(2026, 10, 2)),  # same weekday = today
        ("mon", MON, date(2026, 9, 28)),
        ("thurs", MON, date(2026, 10, 1)),
        ("10/16", MON, date(2026, 10, 16)),
        ("9/28", MON, date(2026, 9, 28)),
        ("9/1", MON, date(2027, 9, 1)),  # past this year -> next year
        ("1/2", et(2026, 12, 30, 10, 0), date(2027, 1, 2)),  # year rollover
        ("10/16/26", MON, date(2026, 10, 16)),
        ("1/15/2027", MON, date(2027, 1, 15)),
        ("oct 16", MON, date(2026, 10, 16)),
        ("Sept 1", MON, date(2027, 9, 1)),
        ("dec5", MON, date(2026, 12, 5)),
        ("2/30", MON, None),
        ("13/1", MON, None),
        ("banana", MON, None),
        ("smarch 3", MON, None),
        ("", MON, None),
    ],
)
def test_resolve_expiry_token(token: str, now: datetime, expected: date | None) -> None:
    assert resolve_expiry_token(token, now) == expected


def test_resolution_uses_the_et_date_not_utc() -> None:
    # Friday 21:00 ET is already Saturday in UTC; "friday" still means that Friday.
    now = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)
    assert resolve_expiry_token("friday", now) == date(2026, 10, 2)


def test_naive_datetimes_rejected() -> None:
    naive = datetime(2026, 9, 28, 10, 0)
    for call in (
        lambda: to_et(naive),
        lambda: is_regular_hours(naive),
        lambda: next_regular_open(naive),
        lambda: resolve_expiry_token("0dte", naive),
        lambda: FixedClock(naive),
    ):
        with pytest.raises(ValueError):
            call()


def test_out_of_calendar_bounds_rejected() -> None:
    with pytest.raises(ValueError):
        session_for(date(2100, 1, 4))


def test_sessions_ahead() -> None:
    assert sessions_ahead(date(2026, 10, 3), 0) == date(2026, 10, 5)
    assert sessions_ahead(date(2026, 11, 25), 1) == date(2026, 11, 27)
    with pytest.raises(ValueError):
        sessions_ahead(date(2026, 10, 5), -1)


def test_fixed_clock() -> None:
    clock = FixedClock(MON)
    clock.advance(timedelta(minutes=5))
    assert clock.now() == MON + timedelta(minutes=5)
