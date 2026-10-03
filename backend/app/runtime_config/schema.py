"""`RuntimeConfig`: every tunable PRD §18 names. Money values have no defaults (PRD open item e)."""

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

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
    """Time budget (F078, from 510 real calls on 2026-10-03: p50 353 ms, p95 450 ms; an earlier
    run p95 688 ms): `queue_timeout_ms` caps the wait for a `typesafe` limiter slot, then
    `timeout_ms` caps one request; `budget_ms` caps the whole Jev step (wait + attempts), so a
    429 retry happens only when a full request still fits. A miss is a model failure: the
    message goes to the fallbacks, never waits longer."""

    model: str | None = "jev-latest"  # TRD §4.3: pin to a dated version once chosen
    timeout_ms: PositiveInt = 700
    queue_timeout_ms: PositiveInt = 250
    budget_ms: PositiveInt = 1200
    enabled: bool = (
        True  # primary interpreter; needs TYPESAFE_API_KEY, else every message falls back
    )

    @model_validator(mode="after")
    def _budget_fits_one_request(self) -> Self:
        if self.budget_ms < self.queue_timeout_ms + self.timeout_ms:
            raise ValueError("jev budget_ms must cover queue_timeout_ms + timeout_ms")
        return self


class LayaConfig(_Strict):
    """Local fallback for when Jev did not settle a message (no answer, low confidence, multiple
    signals). Its answers are weak (it was right on ~27% of labelled messages), so its gate is
    strict: anything it cannot settle continues to the optional LLM, then to review."""

    enabled: bool = True  # loads only on Apple Silicon; a failed load alerts and falls through
    timeout_ms: PositiveInt = 10000  # ~20 questions take 3-13 s on an M4
    t_chat: Probability = Decimal("0.95")
    t_field: Probability = Decimal("0.9")
    t_type: Probability = Decimal("0.9")
    t_link: Probability = Decimal("0.9")


LlmProvider = Literal["anthropic", "openai", "deepseek", "openai_compat"]  # Bedrock: OD-7
NonNegDecimal = Annotated[Decimal, Field(ge=0)]


class ChainEntry(_Strict):
    provider: LlmProvider
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)  # e.g. "extract@v1"
    gate_run: uuid.UUID | None = None  # passing `eval_runs` row (TRD §4.8 rule 5)

    @property
    def model_key(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def model_ref(self) -> str:
        """Matches `eval_runs.model_ref`."""
        return f"{self.model_key}:{self.prompt}"


class RoleConfig(_Strict):
    deadline_ms: PositiveInt | None = None  # shared by every attempt; None = no deadline
    per_attempt_cap_ms: PositiveInt | None = None
    chain: list[ChainEntry] = []  # fallback order; empty = role not configured (OD-7)


class LlmRoles(_Strict):
    extract: RoleConfig = RoleConfig(deadline_ms=3000, per_attempt_cap_ms=2000)
    note: RoleConfig = RoleConfig(deadline_ms=4000)
    prelabel: RoleConfig = RoleConfig()


class Price(_Strict):
    """USD per million tokens."""

    input_per_mtok: NonNegDecimal
    output_per_mtok: NonNegDecimal
    cached_input_per_mtok: NonNegDecimal


class LlmConfig(_Strict):
    """Model registry (TRD §4.8). Keys of `prices`/`params` are "provider:model"."""

    roles: LlmRoles = LlmRoles()
    prices: dict[str, Price] = {}
    params: dict[str, dict[str, Any]] = {}  # passed through to the chat-model constructor
    concurrency: dict[str, PositiveInt] = {}  # provider -> max in-flight calls (default 4)


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
    # F073: one explicit window for production, replay, labeling and prelabel alike (was
    # None -> 0 messages live but 5 in evalkit). The lane author's own messages only (F068).
    context_recent_messages: PositiveInt = 5
    # Sizing: tier name -> dollars (options: default/small/lotto, extensible)
    option_tiers: Tiers | None = None
    stock_tiers: Tiers | None = None
    # Entry validity (C22: X, N, M)
    entry_chase_pct: Pct | None = None
    entry_validity_s: PositiveInt | None = None
    entry_cancel_after_s: PositiveInt | None = None
    # A quote older than this is not used for any actionable decision (FR-12 / OD-12). Alpaca's
    # free indicative options feed is ~15 min delayed: set ~1200 there, a few seconds on OPRA.
    quote_max_age_s: PositiveInt | None = None
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
    t_type: Probability = Decimal("0.8")  # message-type confidence needed to act on a message
    t_link: Probability = Decimal("0.8")
    # Models
    jev: JevConfig = JevConfig()
    laya: LayaConfig = LayaConfig()
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
