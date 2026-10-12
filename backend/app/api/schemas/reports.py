"""Trading report responses (F083). Every figure that can be unknown is `None`, never 0;
`Coverage` says what the numbers do not include."""

import uuid
from datetime import datetime
from typing import Any

from app.api.schemas.base import CamelModel, DiscordId, Num


class WindowOut(CamelModel):
    period: str
    start: datetime
    end: datetime
    label: str
    granularity: str
    timezone: str = "America/New_York"


class Coverage(CamelModel):
    """What a report is and is not made of."""

    window: WindowOut
    mode: str  # shadow | paper | live | all
    as_of: datetime
    first_event_at: datetime | None
    costs_included: bool  # always False today: fees and commissions are not stored
    unpriced_lots: int  # open lots with no fresh quote: their unrealized P&L is unknown
    lots_without_basis: int  # lots with no known average cost: their realized P&L is unknown
    reconcile_adjustments: int  # lot changes made by reconciliation, not by a fill: not in P&L


class CountCompare(CamelModel):
    current: int
    previous: int | None


class MoneyCompare(CamelModel):
    current: Num | None
    previous: Num | None


class LabelCount(CamelModel):
    key: str
    label: str
    count: int


# ----------------------------------------------------------------------------- overview


class Overview(CamelModel):
    coverage: Coverage
    signals: CountCompare  # every signal received (chatter included)
    actionable_signals: CountCompare  # excludes chatter / P&L commentary / TP notices / watches
    accepted_signals: CountCompare  # the decision engine produced an approvable card
    rejected_signals: CountCompare  # the decision engine disabled the card (has a reason)
    acceptance_rate: Num | None  # accepted / (accepted + rejected)
    rejection_rate: Num | None
    top_rejection_reason: str | None
    approved: int  # a person approved the card
    rejected_by_user: int
    pending_cards: int
    orders_submitted: int
    orders_filled: int
    orders_pending: int
    executed_trades: CountCompare  # lots with at least one fill in the window
    buy_fills: int
    sell_fills: int
    executed_notional: Num | None
    fees: Num | None  # None: not captured
    realized_pnl: MoneyCompare
    unrealized_pnl: Num | None  # now, never historical
    unrealized_as_of: datetime
    open_positions: int
    net_pnl: Num | None  # realized + unrealized, before costs
    wins: int
    losses: int
    breakeven: int
    unknown_outcomes: int
    win_rate: Num | None
    avg_win: Num | None
    avg_loss: Num | None
    largest_win: Num | None
    largest_loss: Num | None
    profit_factor: Num | None


# ------------------------------------------------------------------------------ signals


class FunnelStage(CamelModel):
    key: str
    label: str
    count: int
    rate_of_previous: Num | None


class ReasonCount(CamelModel):
    source: str  # decision_engine | user | card_lifecycle | broker
    reason: str
    count: int


class SymbolSignals(CamelModel):
    symbol: str | None
    total: int
    accepted: int
    rejected: int


class ProviderSignals(CamelModel):
    provider_id: DiscordId
    name: str | None
    total: int
    accepted: int
    rejected: int


class SignalOutcomes(CamelModel):
    """What happened to signals the decision engine accepted. Attribution is by signal -> card ->
    entry order -> lot; a lot fed by several signals (or by a manual entry) is not attributed."""

    accepted_signals: int
    with_orders: int
    with_fills: int
    closed_attributed: int
    profitable: int
    losing: int
    breakeven: int
    still_open: int
    attribution_unavailable: int  # filled, but the lot is shared / foreign / has no basis
    realized_pnl: Num | None  # attributed closed lots only
    avg_realized_per_outcome: Num | None
    win_rate: Num | None


class SignalRow(CamelModel):
    signal_id: uuid.UUID
    at: datetime
    provider_id: DiscordId
    symbol: str | None
    msg_type: str
    engine_status: str  # accepted | rejected | no_card
    approval_status: str  # approved | rejected | pending | lapsed | not_applicable
    execution_status: str  # none | ordered | filled
    reason: str | None


class SignalsReport(CamelModel):
    coverage: Coverage
    funnel: list[FunnelStage]
    engine_status: list[LabelCount]
    approval_status: list[LabelCount]
    reasons: list[ReasonCount]
    by_symbol: list[SymbolSignals]
    by_provider: list[ProviderSignals]
    conversion_to_approval: Num | None  # approved / accepted
    conversion_to_execution: Num | None  # filled / accepted
    outcomes: SignalOutcomes
    rows: list[SignalRow]
    total_rows: int


# ------------------------------------------------------------------------------- orders


class OrderRow(CamelModel):
    order_id: uuid.UUID
    at: datetime
    symbol: str
    instrument: str
    side: str
    kind: str
    status: str
    mode: str
    requested_qty: int
    filled_qty: int
    avg_fill_price: Num | None  # None when nothing filled
    executed_notional: Num | None
    fees: Num | None
    broker: str  # who holds the order: the mode's broker
    broker_order_id: str | None
    signal_id: uuid.UUID | None
    lot_id: uuid.UUID | None


class SymbolFills(CamelModel):
    symbol: str
    instrument: str
    buy_qty: int
    sell_qty: int
    avg_buy_price: Num | None
    avg_sell_price: Num | None


class BucketNotional(CamelModel):
    bucket: str
    buy: Num
    sell: Num


class OrdersReport(CamelModel):
    coverage: Coverage
    submitted: int
    filled: int
    partially_filled: int
    rejected: int
    cancelled: int
    pending: int
    order_to_fill_rate: Num | None
    buy_qty_requested: int
    sell_qty_requested: int
    buy_qty_filled: int
    sell_qty_filled: int
    executed_notional: Num | None
    fees: Num | None
    status_distribution: list[LabelCount]
    notional_by_bucket: list[BucketNotional]
    by_symbol: list[SymbolFills]
    broker_rejections: list[ReasonCount]
    rows: list[OrderRow]
    total_rows: int


# --------------------------------------------------------------------------------- pnl


class PnlPoint(CamelModel):
    bucket: str  # ISO ET date / hour
    realized: Num | None
    cumulative: Num | None
    has_activity: bool


class PnlReport(CamelModel):
    coverage: Coverage
    realized: Num | None
    unrealized: Num | None
    net: Num | None
    gross_profit: Num | None
    gross_loss: Num | None
    fees: Num | None
    net_after_costs: Num | None  # None: costs not captured
    wins: int
    losses: int
    breakeven: int
    unknown_outcomes: int
    win_rate: Num | None
    avg_win: Num | None
    avg_loss: Num | None
    largest_win: Num | None
    largest_loss: Num | None
    profit_factor: Num | None
    avg_hold_seconds: Num | None
    series: list[PnlPoint]


# ---------------------------------------------------------------------------- instruments


class InstrumentRow(CamelModel):
    symbol: str
    instrument: str
    signals: int
    accepted_signals: int
    rejected_signals: int
    buy_orders: int
    sell_orders: int
    buy_qty: int
    sell_qty: int
    open_qty: int
    avg_entry_price: Num | None
    avg_exit_price: Num | None
    realized_pnl: Num | None
    unrealized_pnl: Num | None
    net_pnl: Num | None
    fees: Num | None
    wins: int
    losses: int
    closed_trades: int
    win_rate: Num | None
    low_sample: bool
    last_activity: datetime | None


class InstrumentsReport(CamelModel):
    coverage: Coverage
    rows: list[InstrumentRow]
    total_rows: int


# ------------------------------------------------------------------------------- trades


class TradeRow(CamelModel):
    lot_id: uuid.UUID
    symbol: str
    underlying: str
    instrument: str
    contract: str | None
    status: str  # open | closed
    outcome: str  # profit | loss | breakeven | open | unknown
    opened_at: datetime
    closed_at: datetime | None
    requested_qty: int | None
    entry_qty: int
    exit_qty: int
    open_qty: int
    avg_entry_price: Num | None
    avg_exit_price: Num | None
    entry_cost: Num | None  # entry fills x price x multiplier: what was spent
    realized_pnl: Num | None
    unrealized_pnl: Num | None
    net_pnl: Num | None  # realized + unrealized, before costs
    fees: Num | None
    hold_seconds: int | None
    mode: str | None
    provider_id: DiscordId | None
    provider_name: str | None
    signal_id: uuid.UUID | None
    attribution: str  # single | shared | none
    is_foreign: bool


class TradesReport(CamelModel):
    coverage: Coverage
    rows: list[TradeRow]
    total_rows: int


class TradeFill(CamelModel):
    fill_id: uuid.UUID
    order_id: uuid.UUID
    side: str
    qty: int
    price: Num
    filled_at: datetime
    fee: Num | None


class OrderStep(CamelModel):
    status: str
    source: str
    at: datetime
    broker_status: str | None


class TradeOrder(CamelModel):
    order_id: uuid.UUID
    kind: str
    side: str
    order_type: str
    status: str
    mode: str
    requested_qty: int
    filled_qty: int
    remaining_qty: int
    limit_price: Num | None
    stop_price: Num | None
    broker_order_id: str | None
    client_order_id: str
    auto_reason: str | None
    submitted_at: datetime
    steps: list[OrderStep]


class LotStep(CamelModel):
    change: str
    qty_before: int | None
    qty_after: int
    avg_before: Num | None
    avg_after: Num | None
    fill_id: uuid.UUID | None
    at: datetime


class TradeSignal(CamelModel):
    signal_id: uuid.UUID
    msg_type: str
    interpreter: str
    posted_at: datetime
    text: str | None
    provider_id: DiscordId
    provider_name: str | None
    parsed: dict[str, Any]  # the stored SignalFields, verbatim


class TradeDecision(CamelModel):
    card_id: uuid.UUID
    action: str
    card_type: str
    state: str
    disabled_reason: str | None
    warnings: list[str]
    proposed_qty: int | None
    events: list[LabelCount]  # reason code -> times (chronological order not implied)
    decided: str | None  # approve | reject
    decided_at: datetime | None
    approved_qty: int | None  # what the person approved, after edits
    approved_limit_price: Num | None
    edited: bool


class TradeStop(CamelModel):
    kind: str
    level: Num
    qty: int
    status: str
    held_by: str


class TradeAuditEntry(CamelModel):
    at: datetime
    action: str
    user_email: str | None
    resource_type: str
    resource_id: str


class TradeDetail(CamelModel):
    coverage: Coverage
    trade: TradeRow
    signals: list[TradeSignal]  # every signal behind the entry orders; >1 means shared
    decisions: list[TradeDecision]
    orders: list[TradeOrder]
    fills: list[TradeFill]
    lot_history: list[LotStep]
    stops: list[TradeStop]
    audit: list[TradeAuditEntry]
    outcome_explanation: str


# ---------------------------------------------------------------------------- timeseries


class SeriesPoint(CamelModel):
    bucket: str
    has_activity: bool
    values: dict[str, Num | None]


class TimeseriesReport(CamelModel):
    coverage: Coverage
    granularity: str
    metrics: list[str]
    points: list[SeriesPoint]
