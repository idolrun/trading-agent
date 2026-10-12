"""F083: report windows are ET calendar days, start-inclusive, end-exclusive."""

from datetime import UTC, date, datetime

import pytest

from app.api.errors import ApiError
from app.markettime import to_et
from app.reports.period import Granularity, Period, et_midnight, resolve

# Monday 2026-09-28 14:30 UTC = 10:30 ET (EDT, UTC-4)
NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def test_one_day_is_today_so_far_in_et() -> None:
    w = resolve(Period.D1, None, None, NOW)
    assert w.start == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)  # 00:00 ET
    assert w.end == NOW
    assert w.granularity is Granularity.HOUR


def test_just_after_midnight_utc_is_still_the_previous_et_day() -> None:
    w = resolve(Period.D1, None, None, datetime(2026, 9, 29, 2, 0, tzinfo=UTC))  # 22:00 ET Mon
    assert w.start == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)


def test_seven_days_include_today_and_six_before() -> None:
    w = resolve(Period.D7, None, None, NOW)
    assert w.start == et_midnight(date(2026, 9, 22))
    assert w.granularity is Granularity.DAY


def test_previous_window_is_equally_long_and_adjacent() -> None:
    w = resolve(Period.D7, None, None, NOW)
    prev = w.previous
    assert prev is not None and prev.end - prev.start == w.end - w.start
    assert prev.start == et_midnight(date(2026, 9, 15))
    assert prev.end == NOW.replace(day=21)  # shifted back exactly 7 days


def test_all_has_no_previous_and_resolves_its_start_from_data() -> None:
    w = resolve(Period.ALL, None, None, NOW)
    assert w.start is None and w.previous is None
    first = datetime(2026, 3, 2, 15, tzinfo=UTC)
    assert w.resolved(first).start == et_midnight(date(2026, 3, 2))


def test_custom_includes_the_end_date_and_is_end_exclusive() -> None:
    w = resolve(Period.CUSTOM, date(2026, 9, 1), date(2026, 9, 3), NOW)
    assert w.start == et_midnight(date(2026, 9, 1))
    assert w.end == et_midnight(date(2026, 9, 4))


def test_previous_window_steps_back_one_calendar_day_across_fall_back() -> None:
    # 2026-11-01 is 25 hours. Elapsed time would round that up to two days.
    w = resolve(Period.CUSTOM, date(2026, 11, 1), date(2026, 11, 1), NOW)
    prev = w.previous
    assert prev is not None
    assert prev.start == et_midnight(date(2026, 10, 31))
    assert prev.end == w.start


def test_previous_window_keeps_the_end_wall_clock_across_fall_back() -> None:
    # 15:00 ET after the clocks fall back. Shifting by the UTC midnight gap lands at 16:00.
    now = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    w = resolve(Period.D1, None, None, now)
    prev = w.previous
    assert prev is not None and prev.start == et_midnight(date(2026, 10, 31))
    prev_end, this_end = to_et(prev.end), to_et(w.end)
    assert prev_end.date() == date(2026, 10, 31)
    assert prev_end.timetz().replace(tzinfo=None) == this_end.timetz().replace(tzinfo=None)


def test_previous_window_spans_the_fall_back_by_calendar_days() -> None:
    w = resolve(Period.CUSTOM, date(2026, 10, 31), date(2026, 11, 2), NOW)
    prev = w.previous
    assert prev is not None
    assert prev.start == et_midnight(date(2026, 10, 28))
    assert prev.end == w.start


def test_dst_day_is_23_or_25_hours_not_24() -> None:
    # US fall back: 2026-11-01 has 25 hours in ET
    w = resolve(Period.CUSTOM, date(2026, 11, 1), date(2026, 11, 1), NOW)
    assert (w.end - w.start).total_seconds() == 25 * 3600


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        (Period.CUSTOM, None, None),
        (Period.CUSTOM, date(2026, 9, 3), date(2026, 9, 1)),
        (Period.CUSTOM, date(2020, 1, 1), date(2026, 9, 1)),
        (Period.D7, date(2026, 9, 1), None),
    ],
)
def test_invalid_ranges_are_422(period: Period, start: date | None, end: date | None) -> None:
    with pytest.raises(ApiError) as err:
        resolve(period, start, end, NOW)
    assert err.value.status == 422 and err.value.code == "invalid"
