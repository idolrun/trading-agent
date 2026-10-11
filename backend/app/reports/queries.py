"""Report SQL (F083): reads only, no financial rules. Returns plain rows for `service.py`.

Aggregation happens in the database wherever the result is small; the few lists that reach
Python (closed trades in a window, open lots) are narrow rows for one user's book.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import col

from app.cards.models import Approval, Card, CardEvent
from app.execution.models import Fill, Order, OrderEvent, Stop
from app.inbox.models import MessageEvent
from app.interpret.models import Signal
from app.ledger.lots import OPTION_MULTIPLIER
from app.ledger.models import Lot, LotEvent, ProviderPosition
from app.reports.period import Granularity

ENTRY_TYPES = ("entry", "add", "stock_entry")  # signals whose approved card opens or grows a lot
NON_ACTIONABLE = ("chatter", "commentary_pnl", "tp_hit_notice", "out_of_scope_strategy", "watch")
REDUCE = ("fill_reduce", "close")
ET_ZONE = "America/New_York"

_MULT = case((col(Lot.instrument) == "option", OPTION_MULTIPLIER), else_=1)
_ORDER_MULT = case((col(Order.instrument) == "option", OPTION_MULTIPLIER), else_=1)
_OCC_TAIL = r"([ ]*[0-9]{6}[CP][0-9]{8})?$"


@dataclass(frozen=True)
class Filters:
    """`mode` None = every mode."""

    mode: str | None = None
    symbol: str | None = None
    instrument: str | None = None
    provider_id: int | None = None


# ------------------------------------------------------------------------------ helpers


def _symbol_matches(column: Any, symbol: str) -> ColumnElement[bool]:
    """Exact underlying, or an OCC option symbol rooted at it (never a longer ticker)."""
    matches: ColumnElement[bool] = column.op("~")(f"^{re.escape(symbol.upper())}{_OCC_TAIL}")
    return matches


def _order_filters(f: Filters) -> list[ColumnElement[bool]]:
    out: list[ColumnElement[bool]] = []
    if f.mode:
        out.append(col(Order.mode) == f.mode)
    if f.symbol:
        out.append(_symbol_matches(col(Order.broker_symbol), f.symbol))
    if f.instrument:
        out.append(col(Order.instrument) == f.instrument)
    return out


def _lot_filters(f: Filters) -> list[ColumnElement[bool]]:
    out: list[ColumnElement[bool]] = []
    if f.symbol:
        out.append(col(Lot.underlying) == f.symbol.upper())
    if f.instrument:
        out.append(col(Lot.instrument) == f.instrument)
    if f.provider_id is not None:
        out.append(col(Lot.provider_id) == f.provider_id)
    if f.mode:
        o = aliased(Order)  # an alias: callers may already join `orders` in the outer query
        out.append(exists().where(col(o.lot_id) == col(Lot.lot_id), col(o.mode) == f.mode))
    return out


def _bucket(column: Any, gran: Granularity) -> ColumnElement[datetime]:
    """Bucket start as an ET wall-clock timestamp (matches `period.floor_et`)."""
    return func.date_trunc(gran.value, func.timezone(ET_ZONE, column))


def underlying_of(broker_symbol: str) -> str:
    return re.sub(r"[ ]*[0-9]{6}[CP][0-9]{8}$", "", broker_symbol)


# ---------------------------------------------------------------------------- signals


@dataclass(frozen=True)
class SignalGroup:
    """Signals that share every funnel fact; `count` of them."""

    provider_id: int
    symbol: str | None
    instrument: str | None
    actionable: bool
    parsed: bool
    entryish: bool
    has_card: bool
    accepted: bool
    disabled: bool
    approved: bool
    user_rejected: bool
    pending: bool
    lapsed: bool
    reason: str | None
    lapse_reason: str | None
    ordered: bool
    filled: bool
    count: int


def _signal_base(f: Filters, start: datetime, end: datetime) -> Select[Any]:
    """One row per signal with its funnel facts (a signal can have several cards/orders)."""
    pp = aliased(ProviderPosition)
    ce = aliased(CardEvent)
    me = MessageEvent
    order_on = and_(
        col(Order.card_id) == col(Card.card_id),
        col(Order.kind).in_(("entry", "exit")),
        *([col(Order.mode) == f.mode] if f.mode else []),
    )
    q = (
        select(
            col(Signal.signal_id).label("signal_id"),
            col(Signal.provider_id).label("provider_id"),
            col(Signal.msg_type).label("msg_type"),
            col(Signal.instrument).label("instrument"),
            col(me.posted_at).label("at"),
            func.coalesce(
                col(Signal.fields)["underlying"]["value"].as_string(), func.max(col(pp.underlying))
            ).label("symbol"),
            func.coalesce(func.bool_or(col(Card.card_id).is_not(None)), False).label("has_card"),
            func.coalesce(func.bool_or(col(Card.card_type) != "disabled"), False).label("accepted"),
            func.coalesce(func.bool_or(col(Card.card_type) == "disabled"), False).label("disabled"),
            func.coalesce(func.bool_or(col(Approval.decision) == "approve"), False).label(
                "approved"
            ),
            func.coalesce(func.bool_or(col(Approval.decision) == "reject"), False).label(
                "user_rejected"
            ),
            func.coalesce(func.bool_or(col(Card.state) == "pending"), False).label("pending"),
            func.coalesce(
                func.bool_or(col(Card.state).in_(("expired", "invalidated"))), False
            ).label("lapsed"),
            func.max(case((col(Card.card_type) == "disabled", col(Card.disabled_reason)))).label(
                "reason"
            ),
            func.max(col(ce.reason_code)).label("lapse_reason"),
            func.coalesce(func.bool_or(col(Order.status) != "intent"), False).label("ordered"),
            func.coalesce(func.bool_or(col(Order.filled_qty) > 0), False).label("filled"),
        )
        .select_from(Signal)
        .join(
            me,
            and_(
                col(me.message_id) == col(Signal.message_id),
                col(me.event_type) == col(Signal.event_type),
                col(me.event_seq) == col(Signal.event_seq),
            ),
        )
        .outerjoin(Card, col(Card.signal_id) == col(Signal.signal_id))
        .outerjoin(Approval, col(Approval.card_id) == col(Card.card_id))
        .outerjoin(
            ce,
            and_(
                col(ce.card_id) == col(Card.card_id),
                col(ce.to_state).in_(("expired", "invalidated")),
            ),
        )
        .outerjoin(Order, order_on)
        .outerjoin(pp, col(pp.provider_position_id) == col(Card.provider_position_id))
        .where(col(me.posted_at) >= start, col(me.posted_at) < end)
        .group_by(col(Signal.signal_id), col(me.posted_at))
    )
    if f.provider_id is not None:
        q = q.where(col(Signal.provider_id) == f.provider_id)
    if f.instrument:
        q = q.where(col(Signal.instrument) == f.instrument)
    return q


def _symbol_clause(base: Any, symbol: str | None) -> list[ColumnElement[bool]]:
    return [func.upper(base.c.symbol) == symbol.upper()] if symbol else []


async def signal_groups(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[SignalGroup]:
    base = _signal_base(f, start, end).subquery()
    c = base.c
    actionable = c.msg_type.not_in(NON_ACTIONABLE)
    parsed = c.msg_type != "unparsed"
    keys = [
        c.provider_id,
        c.symbol,
        c.instrument,
        actionable,
        parsed,
        c.msg_type.in_(ENTRY_TYPES),
        c.has_card,
        c.accepted,
        c.disabled,
        c.approved,
        c.user_rejected,
        c.pending,
        c.lapsed,
        c.reason,
        c.lapse_reason,
        c.ordered,
        c.filled,
    ]
    q = select(*keys, func.count()).where(*_symbol_clause(base, f.symbol)).group_by(*keys)
    rows = (await db.execute(q)).all()
    return [SignalGroup(*r) for r in rows]


@dataclass(frozen=True)
class SignalRowData:
    signal_id: uuid.UUID
    at: datetime
    provider_id: int
    symbol: str | None
    msg_type: str
    accepted: bool
    disabled: bool
    approved: bool
    user_rejected: bool
    pending: bool
    lapsed: bool
    reason: str | None
    lapse_reason: str | None
    ordered: bool
    filled: bool


async def signal_rows(
    db: AsyncSession,
    f: Filters,
    start: datetime,
    end: datetime,
    *,
    engine: str | None,
    limit: int,
    offset: int,
) -> tuple[list[SignalRowData], int]:
    base = _signal_base(f, start, end).subquery()
    c = base.c
    where = _symbol_clause(base, f.symbol)
    if engine == "accepted":
        where.append(c.accepted)
    elif engine == "rejected":
        where += [~c.accepted, c.disabled]
    elif engine == "no_card":
        where += [~c.accepted, ~c.disabled]
    total = await db.scalar(select(func.count()).select_from(base).where(*where)) or 0
    rows = await db.execute(
        select(
            c.signal_id,
            c.at,
            c.provider_id,
            c.symbol,
            c.msg_type,
            c.accepted,
            c.disabled,
            c.approved,
            c.user_rejected,
            c.pending,
            c.lapsed,
            c.reason,
            c.lapse_reason,
            c.ordered,
            c.filled,
        )
        .where(*where)
        .order_by(c.at.desc(), c.signal_id)
        .limit(limit)
        .offset(offset)
    )
    return [SignalRowData(*r) for r in rows.all()], total


async def signal_buckets(
    db: AsyncSession, f: Filters, start: datetime, end: datetime, gran: Granularity
) -> list[tuple[str, int, int, int]]:
    """(bucket, signals, accepted, rejected) per bucket."""
    base = _signal_base(f, start, end).subquery()
    c = base.c
    bucket = _bucket(c.at, gran)
    rejected = and_(~c.accepted, c.disabled)
    q = (
        select(
            bucket,
            func.count(),
            func.count().filter(c.accepted),
            func.count().filter(rejected),
        )
        .where(*_symbol_clause(base, f.symbol))
        .group_by(bucket)
    )
    return [(str(b), int(n), int(a), int(r)) for b, n, a, r in (await db.execute(q)).all()]


async def provider_names(db: AsyncSession, provider_ids: set[int]) -> dict[int, str]:
    """Newest Discord display name per provider, from the raw payload of their latest message
    that carried one (the name is not stored anywhere else)."""
    if not provider_ids:
        return {}
    name = col(MessageEvent.payload)["message"]["author"]["name"].as_string()
    rows = await db.execute(
        select(col(MessageEvent.provider_id), name)
        .where(col(MessageEvent.provider_id).in_(provider_ids), name.is_not(None))
        .order_by(col(MessageEvent.provider_id), col(MessageEvent.posted_at).desc())
        .distinct(col(MessageEvent.provider_id))
    )
    return {pid: text for pid, text in rows}


# ------------------------------------------------------------------------ realized P&L


def _per_lot() -> Any:
    """Lifetime realized P&L per lot from its reductions: the average before each sell (kept by
    `lot_events`) against the fill price. `missing` = some average was unknown."""
    sold = col(LotEvent.qty_before) - col(LotEvent.qty_after)
    return (
        select(
            col(LotEvent.lot_id).label("lot_id"),
            func.sum((col(Fill.price) - col(LotEvent.avg_before)) * sold).label("raw"),
            func.bool_or(col(LotEvent.avg_before).is_(None)).label("missing"),
            func.sum(sold).label("sold_qty"),
            func.sum(col(Fill.price) * sold).label("sold_value"),
            func.max(col(Fill.filled_at)).label("last_exit_at"),
        )
        .join(Fill, col(Fill.fill_id) == col(LotEvent.fill_id))
        .where(col(LotEvent.change).in_(REDUCE))
        .group_by(col(LotEvent.lot_id))
        .cte("per_lot")
    )


def _realized_expr(per_lot: Any) -> ColumnElement[Decimal]:
    return case((per_lot.c.missing, None), else_=per_lot.c.raw * _MULT)


async def realized_buckets(
    db: AsyncSession,
    f: Filters,
    start: datetime,
    end: datetime,
    gran: Granularity | None,
) -> list[tuple[str | None, str, str, Decimal | None, int]]:
    """Realized P&L of reductions filled in the window, per (bucket, underlying, instrument).
    A group with an unknown average is None. `gran` None = one row per instrument."""
    sold = col(LotEvent.qty_before) - col(LotEvent.qty_after)
    bucket = _bucket(col(Fill.filled_at), gran) if gran else literal(None)
    q = (
        select(
            bucket,
            col(Lot.underlying),
            col(Lot.instrument),
            func.sum((col(Fill.price) - col(LotEvent.avg_before)) * sold * _MULT),
            func.bool_or(col(LotEvent.avg_before).is_(None)),
            func.count(),
        )
        .select_from(LotEvent)
        .join(Fill, col(Fill.fill_id) == col(LotEvent.fill_id))
        .join(Order, col(Order.order_id) == col(Fill.order_id))
        .join(Lot, col(Lot.lot_id) == col(LotEvent.lot_id))
        .where(
            col(LotEvent.change).in_(REDUCE),
            col(Fill.filled_at) >= start,
            col(Fill.filled_at) < end,
            *_lot_filters_for_events(f),
        )
        .group_by(bucket, col(Lot.underlying), col(Lot.instrument))
    )
    return [
        (None if b is None else str(b), u, i, None if missing else pnl, int(n))
        for b, u, i, pnl, missing, n in (await db.execute(q)).all()
    ]


def _lot_filters_for_events(f: Filters) -> list[ColumnElement[bool]]:
    """Event rows already join the fill's order, so the mode is that order's own."""
    out = _lot_filters(Filters(None, f.symbol, f.instrument, f.provider_id))
    if f.mode:
        out.append(col(Order.mode) == f.mode)
    return out


@dataclass(frozen=True)
class ClosedTrade:
    lot_id: uuid.UUID
    underlying: str
    instrument: str
    opened_at: datetime
    closed_at: datetime
    realized: Decimal | None


async def closed_trades(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[ClosedTrade]:
    """Lots fully closed in the window with their lifetime realized P&L (None = no basis)."""
    pl = _per_lot()
    q = (
        select(
            col(Lot.lot_id),
            col(Lot.underlying),
            col(Lot.instrument),
            col(Lot.opened_at),
            col(Lot.closed_at),
            _realized_expr(pl),
        )
        .select_from(Lot)
        .outerjoin(pl, pl.c.lot_id == col(Lot.lot_id))
        .where(
            col(Lot.status) == "closed",
            col(Lot.closed_at) >= start,
            col(Lot.closed_at) < end,
            *_lot_filters(f),
        )
    )
    return [ClosedTrade(*r) for r in (await db.execute(q)).all()]


async def open_lots(db: AsyncSession, f: Filters) -> list[Lot]:
    q = select(Lot).where(col(Lot.status) == "open", col(Lot.qty) > 0, *_lot_filters(f))
    return list((await db.scalars(q)).all())


async def lots_without_basis(db: AsyncSession, f: Filters) -> int:
    q = select(func.count()).select_from(Lot).where(col(Lot.avg_price).is_(None), *_lot_filters(f))
    return int(await db.scalar(q) or 0)


async def reconcile_adjustments(db: AsyncSession, start: datetime, end: datetime) -> int:
    q = select(func.count()).where(
        col(LotEvent.fill_id).is_(None),
        col(LotEvent.change).in_(("reconcile_adjust", "adopt_foreign")),
        col(LotEvent.at) >= start,
        col(LotEvent.at) < end,
    )
    return int(await db.scalar(q) or 0)


async def first_event_at(db: AsyncSession) -> datetime | None:
    first_fill = await db.scalar(select(func.min(col(Fill.filled_at))))
    first_msg = await db.scalar(select(func.min(col(MessageEvent.posted_at))))
    times = [t for t in (first_fill, first_msg) if t is not None]
    return min(times) if times else None


# --------------------------------------------------------------------------- orders/fills


@dataclass(frozen=True)
class FillStats:
    buy_fills: int
    sell_fills: int
    buy_qty: int
    sell_qty: int
    notional: Decimal | None
    lots: int


async def fill_stats(db: AsyncSession, f: Filters, start: datetime, end: datetime) -> FillStats:
    q = (
        select(
            col(Order.side),
            func.count(),
            func.coalesce(func.sum(col(Fill.qty)), 0),
            func.sum(col(Fill.price) * col(Fill.qty) * _ORDER_MULT),
        )
        .select_from(Fill)
        .join(Order, col(Order.order_id) == col(Fill.order_id))
        .where(
            col(Fill.filled_at) >= start,
            col(Fill.filled_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .group_by(col(Order.side))
    )
    by_side = {side: (int(n), int(qty), val) for side, n, qty, val in (await db.execute(q)).all()}
    lots = await db.scalar(
        select(func.count(func.distinct(col(Order.lot_id))))
        .select_from(Fill)
        .join(Order, col(Order.order_id) == col(Fill.order_id))
        .where(
            col(Fill.filled_at) >= start,
            col(Fill.filled_at) < end,
            col(Order.lot_id).is_not(None),
            *_order_filters(f),
            *_provider_order_clause(f),
        )
    )
    values = [v for _, _, v in by_side.values() if v is not None]
    buy, sell = by_side.get("buy", (0, 0, None)), by_side.get("sell", (0, 0, None))
    return FillStats(
        buy[0],
        sell[0],
        buy[1],
        sell[1],
        sum(values, Decimal(0)) if values else None,
        int(lots or 0),
    )


def _provider_order_clause(f: Filters) -> list[ColumnElement[bool]]:
    if f.provider_id is None:
        return []
    return [
        exists().where(
            col(Card.card_id) == col(Order.card_id), col(Card.provider_id) == f.provider_id
        )
    ]


async def order_status_counts(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[tuple[str, str, int, int, int]]:
    """(status, side, orders, requested qty, filled qty) for orders created in the window.
    Reprice children repeat their parent's quantity, so requested qty counts parents only."""
    q = (
        select(
            col(Order.status),
            col(Order.side),
            func.count(),
            func.coalesce(
                func.sum(case((col(Order.parent_order_id).is_(None), col(Order.qty)), else_=0)), 0
            ),
            func.coalesce(func.sum(col(Order.filled_qty)), 0),
        )
        .where(
            col(Order.created_at) >= start,
            col(Order.created_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .group_by(col(Order.status), col(Order.side))
    )
    return [(s, side, int(n), int(r), int(fq)) for s, side, n, r, fq in (await db.execute(q)).all()]


async def fills_by_symbol(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[tuple[str, str, str, int, int, Decimal, datetime]]:
    """(broker symbol, instrument, side, fills, qty, price x qty, last fill) in the window."""
    q = (
        select(
            col(Order.broker_symbol),
            col(Order.instrument),
            col(Order.side),
            func.count(),
            func.sum(col(Fill.qty)),
            func.sum(col(Fill.price) * col(Fill.qty)),
            func.max(col(Fill.filled_at)),
        )
        .select_from(Fill)
        .join(Order, col(Order.order_id) == col(Fill.order_id))
        .where(
            col(Fill.filled_at) >= start,
            col(Fill.filled_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .group_by(col(Order.broker_symbol), col(Order.instrument), col(Order.side))
    )
    return [
        (sym, ins, side, int(n), int(qty), value, last)
        for sym, ins, side, n, qty, value, last in (await db.execute(q)).all()
    ]


async def orders_by_symbol(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[tuple[str, str, str, int]]:
    q = (
        select(col(Order.broker_symbol), col(Order.instrument), col(Order.side), func.count())
        .where(
            col(Order.created_at) >= start,
            col(Order.created_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .group_by(col(Order.broker_symbol), col(Order.instrument), col(Order.side))
    )
    return [(s, i, side, int(n)) for s, i, side, n in (await db.execute(q)).all()]


async def fill_buckets(
    db: AsyncSession, f: Filters, start: datetime, end: datetime, gran: Granularity
) -> list[tuple[str, str, int, Decimal | None]]:
    """(bucket, side, fills, notional)."""
    bucket = _bucket(col(Fill.filled_at), gran)
    q = (
        select(
            bucket,
            col(Order.side),
            func.count(),
            func.sum(col(Fill.price) * col(Fill.qty) * _ORDER_MULT),
        )
        .select_from(Fill)
        .join(Order, col(Order.order_id) == col(Fill.order_id))
        .where(
            col(Fill.filled_at) >= start,
            col(Fill.filled_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .group_by(bucket, col(Order.side))
    )
    return [(str(b), side, int(n), v) for b, side, n, v in (await db.execute(q)).all()]


async def broker_rejections(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[tuple[str, int]]:
    """Rejected orders by the broker status recorded with the rejection. The broker's own
    reason text is not stored."""
    status = (
        select(col(OrderEvent.broker_payload)["broker_status"].as_string())
        .where(
            col(OrderEvent.order_id) == col(Order.order_id), col(OrderEvent.status) == "rejected"
        )
        .order_by(col(OrderEvent.id).desc())
        .limit(1)
        .scalar_subquery()
    )
    rejected = (
        select(func.coalesce(status, "unspecified").label("reason"))
        .where(
            col(Order.status) == "rejected",
            col(Order.created_at) >= start,
            col(Order.created_at) < end,
            *_order_filters(f),
            *_provider_order_clause(f),
        )
        .subquery()
    )
    q = select(rejected.c.reason, func.count()).group_by(rejected.c.reason)
    return [(r, int(n)) for r, n in (await db.execute(q)).all()]


@dataclass(frozen=True)
class OrderRowData:
    order: Order
    signal_id: uuid.UUID | None


async def order_rows(
    db: AsyncSession,
    f: Filters,
    start: datetime,
    end: datetime,
    *,
    side: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[OrderRowData], int]:
    where = [
        col(Order.created_at) >= start,
        col(Order.created_at) < end,
        *_order_filters(f),
        *_provider_order_clause(f),
    ]
    if side:
        where.append(col(Order.side) == side)
    if status:
        where.append(col(Order.status) == status)
    total = await db.scalar(select(func.count()).select_from(Order).where(*where)) or 0
    rows = await db.execute(
        select(Order, col(Card.signal_id))
        .outerjoin(Card, col(Card.card_id) == col(Order.card_id))
        .where(*where)
        .order_by(col(Order.created_at).desc(), col(Order.order_id))
        .limit(limit)
        .offset(offset)
    )
    return [OrderRowData(o, sid) for o, sid in rows.all()], int(total)


# ----------------------------------------------------------------------------- trades


@dataclass(frozen=True)
class TradeData:
    lot: Lot
    realized: Decimal | None
    sold_qty: int
    sold_value: Decimal | None
    entry_qty: int
    entry_value: Decimal | None
    requested_qty: int | None
    mode: str | None


@dataclass(frozen=True)
class TradeQuery:
    start: datetime
    end: datetime
    state: str | None = None  # open | closed
    outcome: str | None = None  # profit | loss | breakeven | open | unknown
    search: str | None = None
    lot_id: uuid.UUID | None = None  # one trade, whatever its dates
    sort: str = "opened_at"
    descending: bool = True
    limit: int = 25
    offset: int = 0


def _entry_stats() -> Any:
    return (
        select(
            col(Order.lot_id).label("lot_id"),
            func.sum(col(Fill.qty)).label("entry_qty"),
            func.sum(col(Fill.qty) * col(Fill.price)).label("entry_value"),
            func.min(col(Order.mode)).label("mode"),
        )
        .join(Fill, col(Fill.order_id) == col(Order.order_id))
        .where(col(Order.side) == "buy", col(Order.lot_id).is_not(None))
        .group_by(col(Order.lot_id))
        .cte("entry_stats")
    )


def _requested() -> Any:
    return (
        select(col(Order.lot_id).label("lot_id"), func.sum(col(Order.qty)).label("requested"))
        .where(
            col(Order.kind) == "entry",
            col(Order.lot_id).is_not(None),
            col(Order.parent_order_id).is_(None),  # a reprice child repeats its parent's qty
        )
        .group_by(col(Order.lot_id))
        .cte("requested")
    )


async def trades_page(db: AsyncSession, f: Filters, t: TradeQuery) -> tuple[list[TradeData], int]:
    pl, es, rq = _per_lot(), _entry_stats(), _requested()
    realized = _realized_expr(pl)
    closed = col(Lot.status) == "closed"
    outcome = case(
        (~closed, "open"),
        (realized.is_(None), "unknown"),
        (realized > 0, "profit"),
        (realized < 0, "loss"),
        else_="breakeven",
    )
    in_window = or_(
        and_(col(Lot.opened_at) >= t.start, col(Lot.opened_at) < t.end),
        and_(col(Lot.closed_at) >= t.start, col(Lot.closed_at) < t.end),
        exists().where(
            col(Order.lot_id) == col(Lot.lot_id),
            col(Fill.order_id) == col(Order.order_id),
            col(Fill.filled_at) >= t.start,
            col(Fill.filled_at) < t.end,
        ),
    )
    where = [in_window, *_lot_filters(f)]
    if t.lot_id is not None:
        where = [col(Lot.lot_id) == t.lot_id]
    if t.state:
        where.append(col(Lot.status) == t.state)
    if t.outcome:
        where.append(outcome == t.outcome)
    if t.search:
        term = t.search.strip()
        like = f"%{term}%"
        sig_match = exists().where(
            col(Order.lot_id) == col(Lot.lot_id),
            col(Card.card_id) == col(Order.card_id),
            cast(col(Card.signal_id), String).ilike(like),
        )
        where.append(
            or_(
                col(Lot.underlying).ilike(like),
                col(Lot.broker_symbol).ilike(like),
                cast(col(Lot.lot_id), String).ilike(like),
                sig_match,
            )
        )
    sort_cols: dict[str, Any] = {
        "opened_at": col(Lot.opened_at),
        "closed_at": col(Lot.closed_at),
        "realized": realized,
        "cost": es.c.entry_value,
        "symbol": col(Lot.underlying),
    }
    key = sort_cols.get(t.sort, col(Lot.opened_at))
    order = (key.desc().nulls_last() if t.descending else key.asc().nulls_last(), col(Lot.lot_id))
    base = (
        select(
            Lot,
            realized,
            pl.c.sold_qty,
            pl.c.sold_value,
            es.c.entry_qty,
            es.c.entry_value,
            rq.c.requested,
            es.c.mode,
        )
        .select_from(Lot)
        .outerjoin(pl, pl.c.lot_id == col(Lot.lot_id))
        .outerjoin(es, es.c.lot_id == col(Lot.lot_id))
        .outerjoin(rq, rq.c.lot_id == col(Lot.lot_id))
        .where(*where)
    )
    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    rows = (await db.execute(base.order_by(*order).limit(t.limit).offset(t.offset))).all()
    return [
        TradeData(
            lot,
            real,
            int(sq or 0),
            sv,
            int(eq or 0),
            ev,
            None if req is None else int(req),
            mode,
        )
        for lot, real, sq, sv, eq, ev, req, mode in rows
    ], int(total)


@dataclass(frozen=True)
class LotAttribution:
    signal_ids: frozenset[uuid.UUID]
    unattributed_orders: int  # entry orders with no signal behind them (manual / system)


async def lot_attribution(
    db: AsyncSession, lot_ids: set[uuid.UUID] | None = None
) -> dict[uuid.UUID, LotAttribution]:
    """Which signals fed each lot, through card -> entry order -> lot."""
    q = (
        select(col(Order.lot_id), col(Card.signal_id), func.count())
        .select_from(Order)
        .outerjoin(Card, col(Card.card_id) == col(Order.card_id))
        .where(col(Order.kind) == "entry", col(Order.lot_id).is_not(None))
        .group_by(col(Order.lot_id), col(Card.signal_id))
    )
    if lot_ids is not None:
        q = q.where(col(Order.lot_id).in_(lot_ids))
    sigs: dict[uuid.UUID, set[uuid.UUID]] = {}
    other: dict[uuid.UUID, int] = {}
    for lot_id, signal_id, n in (await db.execute(q)).all():
        sigs.setdefault(lot_id, set())
        other.setdefault(lot_id, 0)
        if signal_id is None:
            other[lot_id] += int(n)
        else:
            sigs[lot_id].add(signal_id)
    return {k: LotAttribution(frozenset(v), other[k]) for k, v in sigs.items()}


@dataclass(frozen=True)
class SignalLot:
    signal_id: uuid.UUID
    lot_id: uuid.UUID
    lot_closed: bool
    is_foreign: bool
    realized: Decimal | None  # lifetime, None = unknown


async def signal_lots(
    db: AsyncSession, f: Filters, start: datetime, end: datetime
) -> list[SignalLot]:
    """Accepted signals of the window that reached a lot, with that lot's result."""
    pl = _per_lot()
    me = MessageEvent
    q = (
        select(
            col(Signal.signal_id),
            col(Lot.lot_id),
            col(Lot.status) == "closed",
            col(Lot.is_foreign),
            _realized_expr(pl),
        )
        .select_from(Signal)
        .join(
            me,
            and_(
                col(me.message_id) == col(Signal.message_id),
                col(me.event_type) == col(Signal.event_type),
                col(me.event_seq) == col(Signal.event_seq),
            ),
        )
        .join(Card, col(Card.signal_id) == col(Signal.signal_id))
        .join(Order, and_(col(Order.card_id) == col(Card.card_id), col(Order.kind) == "entry"))
        .join(Lot, col(Lot.lot_id) == col(Order.lot_id))
        .outerjoin(pl, pl.c.lot_id == col(Lot.lot_id))
        .where(
            col(me.posted_at) >= start,
            col(me.posted_at) < end,
            col(Card.card_type) != "disabled",
            *_lot_filters(f),
        )
        .distinct()
    )
    if f.provider_id is not None:
        q = q.where(col(Signal.provider_id) == f.provider_id)
    return [SignalLot(*r) for r in (await db.execute(q)).all()]


async def lot_provider(db: AsyncSession, lot_ids: set[uuid.UUID]) -> dict[uuid.UUID, int | None]:
    if not lot_ids:
        return {}
    rows = await db.execute(
        select(col(Lot.lot_id), col(Lot.provider_id)).where(col(Lot.lot_id).in_(lot_ids))
    )
    return {k: v for k, v in rows.all()}


async def stops_for(db: AsyncSession, lot_id: uuid.UUID) -> list[Stop]:
    return list(
        (
            await db.scalars(
                select(Stop).where(col(Stop.lot_id) == lot_id).order_by(col(Stop.created_at))
            )
        ).all()
    )
