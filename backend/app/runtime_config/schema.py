"""`RuntimeConfig`: every tunable PRD §18 names. Money values have no defaults (PRD open item e)."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.base import DiscordId

PositiveInt = Annotated[int, Field(gt=0)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]
Pct = Annotated[Decimal, Field(gt=0, le=100)]
Probability = Annotated[Decimal, Field(ge=0, le=1)]
Snowflake = Annotated[DiscordId, Field(gt=0)]  # serialized as string (JS precision)
Tiers = Annotated[dict[str, PositiveDecimal], Field(min_length=1)]


class TradingMode(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JevConfig(_Strict):
    model: str | None = None
    timeout_ms: PositiveInt = 300
    enabled: bool = False


class LlmConfig(_Strict):
    """Model registry slot; roles, prices, params and validation are added by F031."""

    chains: dict[str, list[str]] = {}  # role -> ["provider:model", …] fallback order


class RuntimeConfig(_Strict):
    """Stored in `config.document`. `None` = not configured yet (no default by design)."""

    # Mode
    trading_mode: TradingMode = TradingMode.SHADOW
    # Feed
    channels: list[Snowflake] = []
    muted_authors: list[Snowflake] = []
    feed_silence_s: PositiveInt | None = None
    stale_entry_s: PositiveInt = 30
    catch_up_backlog_n: PositiveInt | None = None  # OD-15
    poison_retry_limit: PositiveInt = 3
    # Context
    context_recent_messages: PositiveInt | None = None
    # Sizing: tier name -> dollars (options: default/small/lotto, extensible)
    option_tiers: Tiers | None = None
    stock_tiers: Tiers | None = None
    # Entry validity (C22: X, N, M)
    entry_chase_pct: Pct | None = None
    entry_validity_s: PositiveInt | None = None
    entry_cancel_after_s: PositiveInt | None = None
    # Exit (C25)
    exit_card_timeout_s: PositiveInt | None = None
    exit_reprice_interval_s: PositiveInt | None = None
    exit_reprice_step_pct: Pct | None = None
    exit_floor_pct: Pct | None = Field(
        default=None,
        description="OD-20 proposal: % below the mid at exit start, never below one tick.",
    )
    # Expiry (C19). OD-21 proposal: minutes before the session close, so half days work.
    cutoff_1_min_before_close: PositiveInt | None = None
    cutoff_2_min_before_close: PositiveInt | None = None
    # Risk (FR-18)
    max_daily_loss_usd: PositiveDecimal | None = None
    max_open_positions: PositiveInt | None = None
    max_total_exposure_usd: PositiveDecimal | None = None
    # Validation
    identity_price_tolerance_pct: Pct | None = None
    adjacent_strike_ratio: PositiveDecimal | None = None
    symbol_map: dict[str, str] = {"SPX": "SPXW"}
    missing_underlying_proximity_pct: Pct = Decimal(10)
    missing_underlying_lookback_days: PositiveInt | None = None
    # Routing thresholds (TRD §4.4 start values)
    t_chat: Probability = Decimal("0.9")
    t_field: Probability = Decimal("0.8")
    t_link: Probability = Decimal("0.8")
    # Models
    jev: JevConfig = JevConfig()
    llm: LlmConfig = LlmConfig()
    note_guard_enabled: bool = True  # OD-6 (TRD recommendation)
    llm_monthly_budget_usd: PositiveDecimal | None = None  # PRD open item (d)

    @model_validator(mode="after")
    def _cutoff_1_before_cutoff_2(self) -> Self:
        c1, c2 = self.cutoff_1_min_before_close, self.cutoff_2_min_before_close
        if c1 is not None and c2 is not None and c1 <= c2:
            raise ValueError("cutoff 1 must come before cutoff 2 (more minutes before close)")
        return self


def missing_for_entries(config: RuntimeConfig | None) -> list[str]:
    """Unset no-default fields. Non-empty => entry cards are disabled ("config incomplete").

    Conservative by decision (2026-09-28): every no-default field blocks entries. No config
    row at all counts as all of them unset.
    """
    config = config or RuntimeConfig()
    return [name for name in RuntimeConfig.model_fields if getattr(config, name) is None]
