from typing import Literal

from app.api.schemas.base import CamelModel


class HealthDetails(CamelModel):
    status: Literal["ok", "degraded"]
    feed_silence_seconds: float | None  # None when feed health is not running
    flags: dict[str, bool]
    catch_up: bool
    backlog_depth: int
    oldest_unprocessed_seconds: float | None
    heartbeat_age_seconds: float | None
