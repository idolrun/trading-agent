from datetime import datetime
from typing import Literal

from app.api.schemas.base import CamelModel, DiscordId, Num

Window = Literal["session", "day", "7d"]


class StageLatency(CamelModel):
    stage: str  # a pipeline stage, or `total` (received -> pushed)
    path: Literal["fast", "slow"]  # slow = the LLM extractor ran for the message
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float


class QueueWait(CamelModel):
    provider_id: DiscordId
    count: int
    p95_ms: float
    max_ms: float


class MessageCounts(CamelModel):
    total: int
    late: int
    stale: int
    backfilled: int
    failed: int
    duplicates_since_start: int  # in-process counter


class LlmModelStats(CamelModel):
    provider: str
    model: str
    calls: int
    latency_p50_ms: float
    latency_p95_ms: float
    outcomes: dict[str, int]
    error_rate: float  # calls whose outcome is not `ok`
    fallback_calls: int  # attempts after the first of a chain
    spend_usd: Num


class LlmProviderStats(CamelModel):
    provider: str
    breaker_open: bool | None  # None = never used in this process
    slots_since_start: int
    semaphore_waits_since_start: int
    rate_limited_since_start: int  # 429s


class LlmStats(CamelModel):
    models: list[LlmModelStats]
    providers: list[LlmProviderStats]
    slow_path_share: float | None  # messages that needed the LLM / messages interpreted
    spend_month_to_date_usd: Num
    monthly_budget_usd: Num | None


class CardOutcome(CamelModel):
    to_state: str
    reason_code: str
    count: int


class OrderStats(CamelModel):
    by_status: dict[str, int]
    rejected: int
    reprices: int


class ReconciliationStats(CamelModel):
    last_run_at: datetime | None  # None = none since start
    clean: bool | None
    mismatches: int
    unknown_orders: int
    error: str | None
    mismatch_alerts: int  # `reconcile_mismatch` alerts in the window


class PostgresStats(CamelModel):
    connections: int
    lock_waits: int


class HealthDetails(CamelModel):
    status: Literal["ok", "degraded"]
    window: Window
    feed_silence_seconds: float | None  # None when feed health is not running
    feed_heartbeat_age_seconds: float | None
    flags: dict[str, bool]
    catch_up: bool
    backlog_depth: int
    oldest_unprocessed_seconds: float | None
    heartbeat_age_seconds: float | None
    latency: list[StageLatency]
    queue_wait: list[QueueWait]
    messages: MessageCounts
    llm: LlmStats
    card_outcomes: list[CardOutcome]
    orders: OrderStats
    invariant_blocks: int
    reconciliation: ReconciliationStats
    postgres: PostgresStats
