"""Per-provider circuit breaker (TRD §4.8 rule 9): 5 consecutive failures within 60 s open the
circuit for 30 s, then one half-open probe decides."""

from datetime import datetime, timedelta

from app.markettime import Clock

FAILURES_TO_OPEN = 5
FAILURE_WINDOW = timedelta(seconds=60)
OPEN_FOR = timedelta(seconds=30)


class CircuitBreaker:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._failures: list[datetime] = []  # consecutive, oldest first
        self._opened_at: datetime | None = None
        self._probing = False

    def allow(self) -> bool:
        """May a call go out now? Past the open period, lets exactly one probe through."""
        if self._opened_at is None:
            return True
        if self._probing or self._clock.now() - self._opened_at < OPEN_FOR:
            return False
        self._probing = True
        return True

    def success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probing = False

    def failure(self) -> None:
        now = self._clock.now()
        if self._probing:  # the half-open probe failed: open again
            self._probing = False
            self._opened_at = now
            return
        self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW] + [now]
        if len(self._failures) >= FAILURES_TO_OPEN:
            self._opened_at = now
            self._failures.clear()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None
