"""Trading mode <-> broker environment (PRD §17, F051): the mode says which money an order may
touch, the broker adapter + endpoint say which money it would touch. They must agree.

    shadow -> never reaches a broker, any adapter
    paper  -> Alpaca paper endpoint only
    live   -> Alpaca live endpoint only (Settings already demands ENVIRONMENT=production)

`fake` stands for the in-memory adapter: tests only. A dangerous pair is refused, never
corrected. Pure."""

from collections.abc import Callable

from app.runtime_config.schema import RuntimeConfig, TradingMode

FAKE = "fake"
ALPACA_PAPER = "alpaca-paper"
ALPACA_LIVE = "alpaca-live"


def check_mode(
    mode: TradingMode | str, broker_kind: str, *, allow_fake: bool = False
) -> str | None:
    """None = the pair is safe, else why it is not."""
    mode = TradingMode(mode)
    if mode == TradingMode.SHADOW:
        return None
    wanted = ALPACA_PAPER if mode == TradingMode.PAPER else ALPACA_LIVE
    if broker_kind == wanted or (broker_kind == FAKE and allow_fake):
        return None
    return (
        f"trading_mode={mode} needs the {wanted} broker, but this process runs {broker_kind} "
        "(set BROKER_ADAPTER / APCA_API_BASE_URL to match, or change the mode)"
    )


def mode_validator(
    broker_kind: str, *, allow_fake: bool = False
) -> Callable[[RuntimeConfig], list[str]]:
    """For `ConfigService`: a config whose mode this process cannot serve is not saved."""
    return lambda config: [
        why for why in [check_mode(config.trading_mode, broker_kind, allow_fake=allow_fake)] if why
    ]
