"""Shared fixtures for decide() tests (F023, F024): a complete config and signal builders."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.decide.engine import decide
from app.decide.types import DecisionInputs, LotView, ProposedAction, RiskSnapshot
from app.interpret.models import LinkMethod, LinkOutcome
from app.interpret.signal import LinkHint, ParsedSignal, SignalFields
from app.ledger.correlation import LinkDecision
from app.ledger.positions import PositionState
from app.runtime_config.schema import RuntimeConfig
from app.validate.normalize import NormalizedSignal, Price
from app.validate.service import ValidationResult

D = Decimal
POSTED = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
EXP = date(2026, 10, 2)
P_ID = uuid.uuid4()
LOT = uuid.uuid4()
PENNY = D("0.01")
CONFIG = RuntimeConfig(
    feed_silence_s=600,
    catch_up_backlog_n=20,
    context_recent_messages=30,
    option_tiers={"default": D("1000"), "lotto": D("100")},
    stock_tiers={"default": D("5000")},
    entry_chase_pct=D("5"),
    entry_validity_s=60,
    entry_cancel_after_s=120,
    quote_max_age_s=60,
    exit_card_timeout_s=600,
    exit_reprice_interval_s=5,
    exit_reprice_step_pct=D("2"),
    exit_floor_pct=D("20"),
    cutoff_1_min_before_close=30,
    cutoff_2_min_before_close=10,
    max_daily_loss_usd=D("2000"),
    max_open_positions=10,
    max_total_exposure_usd=D("20000"),
    identity_price_tolerance_pct=D("15"),
    adjacent_strike_ratio=D("0.5"),
    missing_underlying_lookback_days=5,
    llm_monthly_budget_usd=D("200"),
)
FLAT = RiskSnapshot(realized_today=D(0), unrealized=D(0), open_positions=0, exposure=D(0))
POSITION = PositionState(
    instrument="option",
    underlying="GOOGL",
    expiry=EXP,
    strike=D("357.5"),
    option_right="call",
    provider_entry_price=D("1.67"),
)
LINKED = LinkDecision(LinkOutcome.LINKED, LinkMethod.JEV, D("0.93"), P_ID)
NEW = LinkDecision(LinkOutcome.NEW_POSITION, LinkMethod.CODE, D(1))


def px(v: str) -> Price:
    return Price(raw=D(v), value=D(v), scale="as_is")


def run(
    msg_type: str,
    *,
    link: LinkDecision | None = LINKED,
    lot_qty: int | None = None,
    validation: dict[str, Any] | None = None,
    fields: SignalFields | None = None,
    field_confidence: dict[str, Decimal] | None = None,
    tick: Decimal | None = PENNY,
    inputs: dict[str, Any] | None = None,
    **norm: Any,
) -> list[ProposedAction]:
    signal = ParsedSignal(
        msg_type=msg_type,
        fields=fields or SignalFields(),
        link_hint=LinkHint(),
        field_confidence=field_confidence or {},
    )
    normalized = NormalizedSignal(
        msg_type=msg_type, instrument=norm.pop("instrument", "option"), **norm
    )
    return decide(
        replace(
            DecisionInputs(
                signal=signal,
                validation=ValidationResult(
                    **({"status": "ok", "normalized": normalized} | (validation or {}))
                ),
                link=link,
                position=POSITION if link is LINKED else None,
                lot=None if lot_qty is None else LotView(LOT, lot_qty, D("1.50")),
                config=CONFIG,
                tick=tick,
                posted_at=POSTED,
                risk=FLAT,
            ),
            **(inputs or {}),
        )
    )


def entry(**kw: Any) -> ProposedAction:
    base: dict[str, Any] = {
        "underlying": "GOOGL",
        "expiry": EXP,
        "strike": D("357.5"),
        "right": "call",
        "entry_price": px("1.67"),
    }
    [card] = run("entry", link=NEW, **(base | kw))
    return card
