from datetime import UTC, datetime, timedelta

from app.dispatcher.classify import age_seconds, error_summary, is_stale, skip_reason
from app.inbox.envelope import Envelope, EventKey
from app.inbox.models import EventType, SkipReason
from app.runtime_config.schema import RuntimeConfig

T0 = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def env(provider: int = 7, channel: int = 55) -> Envelope:
    return Envelope(
        key=EventKey(1, EventType.CREATE, 0),
        provider_id=provider,
        channel_id=channel,
        posted_at=T0,
        received_at=T0,
        text="x",
        payload={},
    )


def test_skip_reasons() -> None:
    config = RuntimeConfig(channels=[55], muted_authors=[9])
    assert skip_reason(env(), config) is None
    assert skip_reason(env(channel=56), config) is SkipReason.CHANNEL_NOT_CONFIGURED
    assert skip_reason(env(provider=9), config) is SkipReason.MUTED_AUTHOR
    assert skip_reason(env(), RuntimeConfig()) is SkipReason.CHANNEL_NOT_CONFIGURED


def test_staleness_boundary() -> None:
    config = RuntimeConfig(stale_entry_s=30)
    assert age_seconds(T0 + timedelta(seconds=31), T0) == 31
    assert not is_stale(29, config)
    assert not is_stale(30, config)
    assert is_stale(31, config)


def test_error_summary_is_one_bounded_line() -> None:
    summary = error_summary(ValueError("first line\nsecond line with detail"))
    assert summary == "ValueError: first line"
    assert len(error_summary(RuntimeError("x" * 1000))) == 300
    assert error_summary(KeyError()) == "KeyError: "
