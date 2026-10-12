"""F024: staleness (AC-19), entry gates (FR-18, C75, OD-27) and warnings (C74)."""

from typing import Any

from app.cards.models import CardAction, CardType
from app.decide.risk import entry_block_reason
from app.decide.types import FlagsView, OpenLotBrief, ProposedAction, RiskSnapshot
from tests.decide_helpers import CONFIG, EXP, FLAT, NEW, D, entry, px, run

FIVE = 5  # contracts the user would type: 5 x 1.75 x 100 = 875

CONTRACT: dict[str, Any] = {
    "underlying": "GOOGL",
    "expiry": EXP,
    "strike": D("357.5"),
    "right": "call",
}


def gated(**inputs: Any) -> ProposedAction:
    return entry(inputs=inputs)


def reason(**inputs: Any) -> str | None:
    card = gated(**inputs)
    assert (card.card_type == CardType.DISABLED) == (card.disabled_reason is not None)
    return card.disabled_reason


def qty_reason(**inputs: Any) -> str | None:
    """The gates that need the user's quantity (C105): they run when it is typed, at preflight
    and at approval, not when the card is created."""
    card = entry().model_copy(update={"qty": FIVE})
    return entry_block_reason(
        inputs.get("config", CONFIG),
        inputs.get("flags", FlagsView()),
        inputs.get("risk", FLAT),
        card,
        option=True,
    )


def risk(**kw: Any) -> RiskSnapshot:
    base: dict[str, Any] = {
        "realized_today": D(0),
        "unrealized": D(0),
        "open_positions": 0,
        "exposure": D(0),
    }
    return RiskSnapshot(**(base | kw))


def test_stale_entry_no_card_stale_exit_late() -> None:
    """AC-19: entry processed 45 s after post -> no card; exit -> card with `late`."""
    stale = {"stale": True}
    assert run("entry", link=NEW, inputs=stale, entry_price=px("1.67"), **CONTRACT) == []
    assert run("entry", lot_qty=2, inputs=stale, entry_price=px("1.67"), **CONTRACT) == []  # add
    [exit_card] = run("full_exit", lot_qty=2, instrument=None, inputs={"stale": True})
    assert exit_card.action == CardAction.CLOSE and exit_card.labels == ["late"]
    [late] = run("stop_cancel", lot_qty=2, instrument=None, inputs={"late": True})
    assert late.labels == ["late"]


def test_each_gate_disables_with_its_reason() -> None:
    assert reason() is None
    partial = CONFIG.model_copy(update={"max_open_positions": None})
    assert reason(config=partial) == "config incomplete: max_open_positions"
    assert reason(flags=FlagsView(kill_switch=True)) == "kill switch active"
    assert reason(flags=FlagsView(entries_blocked=True)) == (
        "entries blocked: reconciliation mismatch"
    )
    assert reason(risk=None) == "risk unknown: quote missing"
    assert reason(risk=risk(unrealized=None)) == "risk unknown: quote missing"
    assert reason(risk=risk(exposure=None)) == "risk unknown: cost basis missing"
    assert reason(risk=risk(realized_today=D(-1500), unrealized=D(-500))) == (
        "max daily loss reached"
    )
    assert reason(risk=risk(realized_today=D(-1500), unrealized=D(-499))) is None
    assert reason(risk=risk(open_positions=10)) == "max open positions"
    # no quantity yet: exposure cannot be judged at creation
    assert reason(risk=risk(exposure=D(19200))) is None
    # 5 contracts x 1.75 x 100 = 875 on top of 19,200 > 20,000
    assert qty_reason(risk=risk(exposure=D(19200))) == "max exposure"
    assert qty_reason(risk=risk(exposure=D(19125))) is None


def test_gates_order_and_add_rules() -> None:
    both = FlagsView(kill_switch=True, entries_blocked=True)
    assert reason(flags=both) == "kill switch active"  # first failure wins
    full = {"risk": risk(open_positions=10)}
    [add] = run("entry", lot_qty=2, inputs=full, entry_price=px("1.67"), **CONTRACT)
    assert add.action == CardAction.ADD and add.disabled_reason is None  # add opens nothing


def test_kill_switch_disables_entries_only() -> None:
    """OD-27: exits are never missed."""
    flags = FlagsView(kill_switch=True)
    [exit_card] = run("full_exit", lot_qty=2, instrument=None, inputs={"flags": flags})
    assert exit_card.card_type == CardType.NORMAL and exit_card.qty == 2


def test_foreign_lot_counts_toward_limits() -> None:
    """C47: the snapshot includes foreign lots, so their count blocks entries like any lot."""
    lots = tuple(OpenLotBrief("AAPL", None) for _ in range(10))
    assert reason(risk=risk(open_positions=10, lots=lots)) == "max open positions"


def test_opposite_direction_warning() -> None:
    puts = risk(lots=(OpenLotBrief("GOOGL", "put"),))
    assert "opposite_direction:GOOGL" in gated(risk=puts).warnings
    same = risk(lots=(OpenLotBrief("GOOGL", "call"), OpenLotBrief("META", "put")))
    assert gated(risk=same).warnings == []
    assert FLAT.lots == ()


def test_buying_power_gate() -> None:
    """The order costs 5 x 1.75 x 100 = 875: the broker account must cover it."""
    account = {"account_required": True, "equity": D(50000), "cash": D(900)}
    assert qty_reason(risk=risk(buying_power=D(875), **account)) is None
    assert qty_reason(risk=risk(buying_power=D(874), **account)) == "insufficient buying power"
    assert qty_reason(risk=risk(buying_power=None, **account)) == (
        "risk unknown: account unavailable"
    )
    # options use their own buying power when the broker states one
    own = risk(buying_power=D(100000), options_buying_power=D(500), **account)
    assert qty_reason(risk=own) == "insufficient buying power"
    # a snapshot built without a broker (pure unit inputs) does not judge the account
    assert reason(risk=risk()) is None


def test_exposure_includes_working_orders_in_the_projection() -> None:
    """Limit 2,000: 1,200 in lots + 500 working leaves 300; this order (875) must not fit."""
    cfg = CONFIG.model_copy(update={"max_total_exposure_usd": D(2000)})
    snap = risk(exposure=D(1700), working_exposure=D(500))  # exposure already holds the working 500
    assert qty_reason(config=cfg, risk=snap) == "max exposure"
    assert qty_reason(config=cfg, risk=risk(exposure=D(1100))) is None  # 1,100 + 875 = 1,975
