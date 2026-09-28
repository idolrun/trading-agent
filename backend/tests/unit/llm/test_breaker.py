from datetime import UTC, datetime, timedelta

from app.llm.breaker import CircuitBreaker
from app.markettime import FixedClock


def test_five_failures_in_60s_open_for_30s_then_one_half_open_probe() -> None:
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    breaker = CircuitBreaker(clock)
    for _ in range(4):
        breaker.failure()
        clock.advance(timedelta(seconds=10))
    assert breaker.allow()
    breaker.failure()  # 5th within 60 s
    assert not breaker.allow()

    clock.advance(timedelta(seconds=29))
    assert not breaker.allow()
    clock.advance(timedelta(seconds=1))
    assert breaker.allow()  # the probe
    assert not breaker.allow()  # only one probe at a time
    breaker.failure()  # probe failed: open again for 30 s
    clock.advance(timedelta(seconds=29))
    assert not breaker.allow()
    clock.advance(timedelta(seconds=1))
    assert breaker.allow()
    breaker.success()
    assert breaker.allow() and breaker.allow() and not breaker.is_open


def test_failures_spread_over_more_than_60s_do_not_open() -> None:
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    breaker = CircuitBreaker(clock)
    for _ in range(10):
        breaker.failure()
        clock.advance(timedelta(seconds=16))  # never 5 within 60 s
    assert breaker.allow()


def test_success_resets_the_consecutive_count() -> None:
    clock = FixedClock(datetime(2026, 9, 28, 14, tzinfo=UTC))
    breaker = CircuitBreaker(clock)
    for _ in range(4):
        breaker.failure()
    breaker.success()
    breaker.failure()
    assert breaker.allow()
