from datetime import UTC, datetime, timedelta

from app.inbox.repo import Backlog
from app.ingest.feed_health import evaluate
from app.runtime_config.schema import RuntimeConfig

T0 = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)  # 15:00 ET
CFG = RuntimeConfig(feed_silence_s=60, catch_up_backlog_n=20, stale_entry_s=30)
EMPTY = Backlog(0, None)


def feed(gap_s: float, in_hours: bool = True, config: RuntimeConfig = CFG) -> str | None:
    return evaluate(T0 + timedelta(seconds=gap_s), T0, EMPTY, config, in_hours).feed


def test_silence_boundary_in_hours() -> None:
    assert feed(60) == "ok"  # exactly N s: not yet silent
    assert feed(60.001) == "silent"


def test_silence_outside_hours_is_held() -> None:
    assert feed(3600, in_hours=False) == "hold"
    assert feed(10, in_hours=False) == "ok"


def test_unconfigured_checks_are_skipped() -> None:
    state = evaluate(T0, T0, Backlog(500, T0), RuntimeConfig(), True)
    assert (state.feed, state.catch_up) == (None, None)


def test_catch_up_thresholds() -> None:
    def catch_up(count: int, oldest_age_s: float | None) -> bool | None:
        oldest = None if oldest_age_s is None else T0 - timedelta(seconds=oldest_age_s)
        return evaluate(T0, T0, Backlog(count, oldest), CFG, True).catch_up

    assert catch_up(20, 5) is False
    assert catch_up(21, 5) is True  # count > N
    assert catch_up(1, 30) is False
    assert catch_up(1, 31) is True  # oldest older than stale_entry_s
    assert catch_up(0, None) is False
