"""Pure dispatcher decisions: skip reasons, message age, staleness, error summaries."""

from datetime import datetime

from app.inbox.envelope import Envelope
from app.inbox.models import SkipReason
from app.runtime_config.schema import RuntimeConfig


def skip_reason(env: Envelope, config: RuntimeConfig) -> SkipReason | None:
    """FR-2: stored and shown in the feed, never interpreted."""
    if env.channel_id not in config.channels:
        return SkipReason.CHANNEL_NOT_CONFIGURED
    if env.provider_id in config.muted_authors:
        return SkipReason.MUTED_AUTHOR
    return None


def age_seconds(started_at: datetime, posted_at: datetime) -> float:
    return (started_at - posted_at).total_seconds()


def is_stale(age_s: float, config: RuntimeConfig) -> bool:
    return age_s > config.stale_entry_s


def error_summary(exc: BaseException) -> str:
    """Short, single-line error for `inbox.last_error` (no tracebacks, bounded size)."""
    first_line = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {first_line}"[:300]
