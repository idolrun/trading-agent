"""F034 note guard (C80, OD-6): unknown tickers, prices or expiries hide the note."""

from datetime import UTC, datetime

from app.interpret.note import allowed_from, note_guard
from app.runtime_config.schema import RuntimeConfig

CFG = RuntimeConfig()
NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)  # a Monday
SOURCE = ["spy 770c .95 lotto, sl 769 spy"]
FIELDS = ["spy", "770", "call", "0.95", "769", "lotto", "2026-09-28"]


def guard(
    note: str, fields: list[str] = FIELDS, texts: list[str] = SOURCE
) -> tuple[bool, tuple[str, ...]]:
    result = note_guard(note, allowed_from(fields, texts, CFG, NOW), CFG, NOW)
    return result.shown, result.offending


def test_note_with_validated_values_is_shown() -> None:
    assert guard("Provider bought SPY 770C at 0.95, lotto size, stop at SPY 769.") == (True, ())
    assert guard("Provider bought SPY 770C at $.95 expiring today (0dte).")[0]
    assert guard("Up 40% already, the provider says it is a quick scalp.")[0]  # % is not a price


def test_note_naming_values_not_in_fields_or_sources_is_hidden() -> None:
    assert guard("Provider bought SPY 771C at 0.95") == (False, ("771C",))
    assert guard("Provider paid $1.10 for SPY 770C") == (False, ("1.10",))
    assert guard("Similar to the QQQ trade earlier") == (False, ("QQQ",))
    assert guard("SPY 770C expiring friday") == (False, ("friday",))


def test_symbol_map_applies_to_note_tickers() -> None:
    shown, _ = guard("Provider bought SPX 5800C", ["SPXW", "5800"], ["spx 5800c"])
    assert shown
