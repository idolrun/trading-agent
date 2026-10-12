import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.runtime_config.schema import RuntimeConfig, TradingMode, missing_for_entries

EXAMPLE = Path(__file__).parents[3] / "config.example.json"

# Every PRD §18 value that has no default (PRD open item e).
NO_DEFAULT = {
    "feed_silence_s",
    "catch_up_backlog_n",
    "entry_chase_pct",
    "entry_validity_s",
    "entry_cancel_after_s",
    "quote_max_age_s",
    "exit_card_timeout_s",
    "exit_reprice_interval_s",
    "exit_reprice_step_pct",
    "exit_floor_pct",
    "cutoff_1_min_before_close",
    "cutoff_2_min_before_close",
    "max_daily_loss_usd",
    "max_open_positions",
    "max_total_exposure_usd",
    "identity_price_tolerance_pct",
    "adjacent_strike_ratio",
    "missing_underlying_lookback_days",
    "llm_monthly_budget_usd",
}

COMPLETE = {
    "feed_silence_s": 600,
    "catch_up_backlog_n": 20,
    "context_recent_messages": 30,
    "entry_chase_pct": "5",
    "entry_validity_s": 60,
    "entry_cancel_after_s": 120,
    "quote_max_age_s": 60,
    "exit_card_timeout_s": 300,
    "exit_reprice_interval_s": 5,
    "exit_reprice_step_pct": "2",
    "exit_floor_pct": "20",
    "cutoff_1_min_before_close": 30,
    "cutoff_2_min_before_close": 10,
    "max_daily_loss_usd": "2000",
    "max_open_positions": 10,
    "max_total_exposure_usd": "20000",
    "identity_price_tolerance_pct": "15",
    "adjacent_strike_ratio": "0.5",
    "missing_underlying_lookback_days": 5,
    "llm_monthly_budget_usd": "200",
}


def test_defaults() -> None:
    c = RuntimeConfig()
    assert c.trading_mode is TradingMode.SHADOW
    assert (c.stale_entry_s, c.poison_retry_limit) == (30, 3)
    assert c.symbol_map == {"SPX": "SPXW"}
    assert c.missing_underlying_proximity_pct == Decimal(10)
    assert (c.t_chat, c.t_field, c.t_link) == (Decimal("0.9"), Decimal("0.8"), Decimal("0.8"))
    assert (c.jev.enabled, c.jev.model, c.laya.enabled) == (True, "jev-1.13.0", True)
    assert c.llm.roles.extract.chain == [] and c.llm.roles.extract.deadline_ms == 3000
    assert c.note_guard_enabled is True
    assert c.channels == c.muted_authors == []


def test_fresh_config_blocks_entries_on_every_no_default_value() -> None:
    assert set(missing_for_entries(RuntimeConfig())) == NO_DEFAULT
    assert set(missing_for_entries(None)) == NO_DEFAULT


def test_missing_for_entries_lists_each_unset_field() -> None:
    assert missing_for_entries(RuntimeConfig.model_validate(COMPLETE)) == []
    for field in NO_DEFAULT:
        partial = {k: v for k, v in COMPLETE.items() if k != field}
        assert missing_for_entries(RuntimeConfig.model_validate(partial)) == [field]


def test_retired_sizing_tiers_are_dropped_on_read() -> None:
    """C105: documents saved before the user typed every quantity still load."""
    config = RuntimeConfig.model_validate({"option_tiers": {"default": "500"}, "stock_tiers": {}})
    assert not hasattr(config, "option_tiers") and "stock_tiers" not in config.model_dump()


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"tiers": {"default": "500"}})
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"jev": {"enabled": True, "temperature": 0}})


@pytest.mark.parametrize(
    "bad",
    [
        {"max_daily_loss_usd": "0"},
        {"entry_chase_pct": "101"},
        {"t_link": "1.5"},
        {"channels": [0]},
        {"cutoff_1_min_before_close": 10, "cutoff_2_min_before_close": 30},
    ],
)
def test_invalid_values_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate(bad)


def test_snowflakes_round_trip_as_strings() -> None:
    c = RuntimeConfig.model_validate({"channels": [1234567890123456789]})
    dumped = c.model_dump(mode="json")
    assert dumped["channels"] == ["1234567890123456789"]
    assert RuntimeConfig.model_validate(dumped) == c


def test_example_file_is_valid_and_complete_in_shape() -> None:
    example = json.loads(EXAMPLE.read_text())
    assert RuntimeConfig.model_validate(example) == RuntimeConfig()
    assert example.keys() == RuntimeConfig.model_fields.keys()


def test_one_context_window_everywhere() -> None:
    """F073: an explicit default; no `None` that one caller reads as 0 and another as 5."""
    assert RuntimeConfig().context_recent_messages == 5
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"context_recent_messages": None})
