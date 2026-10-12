"""Trading report use cases (F083). Assembles `queries` rows with `pnl` math; read-only.

Nothing here submits, changes or cancels anything. Valuation reads quotes (never a broker order).
"""

import asyncio
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.api.errors import ApiError
from app.api.schemas import reports as out
from app.auth.models import AuditLog
from app.cards.models import Approval, Card, CardEvent
from app.decide.risk_snapshot import mark_lot
from app.execution.models import OPEN_ORDER_STATUSES, Fill, Order, OrderEvent
from app.inbox.models import MessageEvent
from app.interpret.models import Signal
from app.ledger.models import Lot, LotEvent
from app.marketdata.port import MarketData
from app.reports import pnl, queries
from app.reports.period import (
    Granularity,
    Window,
    bucket_key,
    bucket_keys,
    floor_et,
)
from app.reports.queries import Filters, SignalGroup

ZERO = Decimal(0)
SIGNAL_ROW_LIMIT = 25
TIMESERIES_METRICS = (
    "realized_pnl",
    "cumulative_pnl",
    "fills_buy",
    "fills_sell",
    "notional_buy",
    "notional_sell",
    "wins",
    "losses",
    "breakeven",
    "signals",
    "accepted_signals",
    "rejected_signals",
)
INSTRUMENT_SORTS = {"net_pnl", "realized_pnl", "win_rate", "closed_trades", "buy_qty", "symbol"}


@dataclass(frozen=True)
class Scope:
    """Everything a report is computed for."""

    window: Window
    filters: Filters
    mode_label: str  # "all" or the mode name


@dataclass(frozen=True)
class Valuation:
    unrealized: Decimal | None  # None when any open lot has no fresh quote
    per_lot: dict[uuid.UUID, Decimal | None]
    open_lots: int
    unpriced: int
    as_of: datetime


class ReportService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        marketdata: MarketData,
        max_quote_age_s: int | None,
        now: datetime,
    ) -> None:
        self._sessions = sessions
        self._md = marketdata
        self._max_age = max_quote_age_s
        self._now = now

    # ------------------------------------------------------------------ shared pieces

    async def _resolve(self, scope: Scope) -> Scope:
        """`all` starts at the first recorded event."""
        if scope.window.start is not None:
            return scope
        async with self._sessions() as db:
            first = await queries.first_event_at(db)
        return Scope(scope.window.resolved(first), scope.filters, scope.mode_label)

    async def _valuation(self, f: Filters, lots: list[Lot] | None = None) -> Valuation:
        if lots is None:
            async with self._sessions() as db:  # closed before any quote is asked for
                lots = await queries.open_lots(db, f)
        marks = await asyncio.gather(
            *(mark_lot(self._md, lot, self._now, self._max_age) for lot in lots)
        )
        per_lot = {
            lot.lot_id: pnl.unrealized_amount(
                lot.avg_price, mark, lot.qty, pnl.multiplier(lot.instrument)
            )
            for lot, mark in zip(lots, marks, strict=True)
        }
        unpriced = sum(1 for v in per_lot.values() if v is None)
        return Valuation(pnl.total(per_lot.values()), per_lot, len(lots), unpriced, self._now)

    async def _coverage(self, db: AsyncSession, scope: Scope, unpriced: int) -> out.Coverage:
        w = scope.window
        assert w.start is not None  # noqa: S101 — resolved before use
        return out.Coverage(
            window=out.WindowOut(
                period=w.period.value,
                start=w.start,
                end=w.end,
                label=w.label,
                granularity=w.granularity.value,
            ),
            mode=scope.mode_label,
            as_of=self._now,
            first_event_at=await queries.first_event_at(db),
            costs_included=False,
            unpriced_lots=unpriced,
            lots_without_basis=await queries.lots_without_basis(db, scope.filters),
            reconcile_adjustments=await queries.reconcile_adjustments(db, w.start, w.end),
        )

    # ---------------------------------------------------------------------- overview

    async def overview(self, scope: Scope) -> out.Overview:
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        prev = w.previous
        val = await self._valuation(f)
        async with self._sessions() as db:
            groups = await queries.signal_groups(db, f, w.start, w.end)
            cur = _counts(groups)
            prev_counts = (
                _counts(await queries.signal_groups(db, f, prev.start, prev.end))
                if prev and prev.start
                else None
            )
            fills = await queries.fill_stats(db, f, w.start, w.end)
            prev_fills = (
                await queries.fill_stats(db, f, prev.start, prev.end)
                if prev and prev.start
                else None
            )
            realized = await self._realized_total(db, f, w.start, w.end)
            prev_realized = (
                await self._realized_total(db, f, prev.start, prev.end)
                if prev and prev.start
                else None
            )
            statuses = await queries.order_status_counts(db, f, w.start, w.end)
            closed = await queries.closed_trades(db, f, w.start, w.end)
            coverage = await self._coverage(db, scope, val.unpriced)
        summary = pnl.summarize(
            [pnl.ClosedTrade(c.realized, c.closed_at - c.opened_at) for c in closed]
        )
        reasons = _signal_reasons(groups)
        by_status = Counter[str]()
        for status, _side, n, _req, _filled in statuses:
            by_status[status] += n
        decided = cur.accepted + cur.rejected
        return out.Overview(
            coverage=coverage,
            signals=out.CountCompare(current=cur.total, previous=prev_counts and prev_counts.total),
            actionable_signals=out.CountCompare(
                current=cur.actionable, previous=prev_counts and prev_counts.actionable
            ),
            accepted_signals=out.CountCompare(
                current=cur.accepted, previous=prev_counts and prev_counts.accepted
            ),
            rejected_signals=out.CountCompare(
                current=cur.rejected, previous=prev_counts and prev_counts.rejected
            ),
            acceptance_rate=pnl.rate(cur.accepted, decided),
            rejection_rate=pnl.rate(cur.rejected, decided),
            top_rejection_reason=next(
                (r.reason for r in reasons if r.source == "decision_engine"), None
            ),
            approved=cur.approved,
            rejected_by_user=cur.user_rejected,
            pending_cards=cur.pending,
            orders_submitted=sum(n for s, n in by_status.items() if s != "intent"),
            orders_filled=by_status["filled"],
            orders_pending=sum(by_status[s] for s in OPEN_ORDER_STATUSES),
            executed_trades=out.CountCompare(
                current=fills.lots, previous=prev_fills.lots if prev_fills else None
            ),
            buy_fills=fills.buy_fills,
            sell_fills=fills.sell_fills,
            executed_notional=fills.notional,
            fees=None,
            realized_pnl=out.MoneyCompare(current=realized, previous=prev_realized),
            unrealized_pnl=val.unrealized,
            unrealized_as_of=val.as_of,
            open_positions=val.open_lots,
            net_pnl=_add(realized, val.unrealized),
            wins=summary.wins,
            losses=summary.losses,
            breakeven=summary.breakeven,
            unknown_outcomes=summary.unknown,
            win_rate=summary.win_rate,
            avg_win=summary.avg_win,
            avg_loss=summary.avg_loss,
            largest_win=summary.largest_win,
            largest_loss=summary.largest_loss,
            profit_factor=summary.profit_factor,
        )

    async def _realized_total(
        self, db: AsyncSession, f: Filters, start: datetime, end: datetime
    ) -> Decimal | None:
        rows = await queries.realized_buckets(db, f, start, end, None)
        return pnl.total(r[3] for r in rows)

    # ----------------------------------------------------------------------- signals

    async def signals(self, scope: Scope, *, engine: str | None, page: int) -> out.SignalsReport:
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        async with self._sessions() as db:
            groups = await queries.signal_groups(db, f, w.start, w.end)
            rows, total = await queries.signal_rows(
                db,
                f,
                w.start,
                w.end,
                engine=engine,
                limit=SIGNAL_ROW_LIMIT,
                offset=(page - 1) * SIGNAL_ROW_LIMIT,
            )
            broker = await queries.broker_rejections(db, f, w.start, w.end)
            outcomes = await self._outcomes(db, f, w, groups)
            names = await queries.provider_names(db, {g.provider_id for g in groups})
            coverage = await self._coverage(db, scope, 0)
        c = _counts(groups)
        closed = outcomes.profitable + outcomes.losing + outcomes.breakeven
        stages = [
            ("received", "Signals received", c.total),
            ("parsed", "Parsed", c.parsed),
            ("actionable", "Actionable", c.actionable),
            ("accepted", "Accepted by the decision engine", c.accepted),
            ("approved", "Approved by you", c.approved),
            ("ordered", "Order submitted", c.ordered),
            ("filled", "Order filled (fully or partly)", c.filled),
            ("closed", "Position closed (entry signals)", outcomes.closed_attributed),
            ("pnl", "Profit or loss determined", closed),
        ]
        funnel = [
            out.FunnelStage(
                key=key,
                label=label,
                count=n,
                rate_of_previous=pnl.rate(n, stages[i - 1][2]) if i else None,
            )
            for i, (key, label, n) in enumerate(stages)
        ]
        engine_status = [
            out.LabelCount(key="accepted", label="Accepted", count=c.accepted),
            out.LabelCount(key="rejected", label="Rejected", count=c.rejected),
            out.LabelCount(key="no_card", label="Actionable, no card", count=c.no_card),
            out.LabelCount(
                key="not_actionable", label="Not actionable", count=c.total - c.actionable
            ),
        ]
        approval_status = [
            out.LabelCount(key="approved", label="Approved", count=c.approved),
            out.LabelCount(key="rejected", label="Rejected by you", count=c.user_rejected),
            out.LabelCount(key="pending", label="Pending", count=c.pending),
            out.LabelCount(key="lapsed", label="Expired or invalidated", count=c.lapsed),
        ]
        reasons = _signal_reasons(groups) + [
            out.ReasonCount(source="broker", reason=r, count=n) for r, n in broker
        ]
        return out.SignalsReport(
            coverage=coverage,
            funnel=funnel,
            engine_status=engine_status,
            approval_status=approval_status,
            reasons=sorted(reasons, key=lambda r: -r.count),
            by_symbol=_by_symbol(groups),
            by_provider=_by_provider(groups, names),
            conversion_to_approval=pnl.rate(c.approved, c.accepted),
            conversion_to_execution=pnl.rate(c.filled, c.accepted),
            outcomes=outcomes,
            rows=[_signal_row(r) for r in rows],
            total_rows=total,
        )

    async def _outcomes(
        self, db: AsyncSession, f: Filters, w: Window, groups: list[SignalGroup]
    ) -> out.SignalOutcomes:
        """What happened to accepted entry/add signals (attribution rules in F083 doc §6)."""
        assert w.start is not None  # noqa: S101
        entry = [g for g in groups if g.entryish and g.accepted]
        links = await queries.signal_lots(db, f, w.start, w.end)
        attribution = await queries.lot_attribution(db, {link.lot_id for link in links})
        by_signal: dict[uuid.UUID, list[queries.SignalLot]] = defaultdict(list)
        for link in links:
            by_signal[link.signal_id].append(link)
        profitable = losing = breakeven = still_open = unavailable = 0
        realized: list[Decimal] = []
        for signal_id, lots in by_signal.items():
            if any(
                not _single(attribution.get(lot.lot_id), signal_id, lot.is_foreign) for lot in lots
            ):
                unavailable += 1
            elif any(not lot.lot_closed for lot in lots):
                still_open += 1
            elif any(lot.realized is None for lot in lots):
                unavailable += 1
            else:
                result = sum((lot.realized or ZERO for lot in lots), ZERO)
                realized.append(result)
                profitable += result > 0
                losing += result < 0
                breakeven += result == 0
        closed = profitable + losing + breakeven
        return out.SignalOutcomes(
            accepted_signals=sum(g.count for g in entry),
            with_orders=sum(g.count for g in entry if g.ordered),
            with_fills=sum(g.count for g in entry if g.filled),
            closed_attributed=closed,
            profitable=profitable,
            losing=losing,
            breakeven=breakeven,
            still_open=still_open,
            attribution_unavailable=unavailable,
            realized_pnl=sum(realized, ZERO) if realized else None,
            avg_realized_per_outcome=sum(realized, ZERO) / closed if closed else None,
            win_rate=pnl.rate(profitable, closed),
        )

    # ------------------------------------------------------------------------ orders

    async def orders(
        self, scope: Scope, *, side: str | None, status: str | None, page: int, page_size: int
    ) -> out.OrdersReport:
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        async with self._sessions() as db:
            counts = await queries.order_status_counts(db, f, w.start, w.end)
            fills = await queries.fill_stats(db, f, w.start, w.end)
            by_symbol = await queries.fills_by_symbol(db, f, w.start, w.end)
            buckets = await queries.fill_buckets(db, f, w.start, w.end, w.granularity)
            broker = await queries.broker_rejections(db, f, w.start, w.end)
            rows, total = await queries.order_rows(
                db,
                f,
                w.start,
                w.end,
                side=side,
                status=status,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            coverage = await self._coverage(db, scope, 0)
        by_status: Counter[str] = Counter()
        requested: Counter[str] = Counter()
        filled: Counter[str] = Counter()
        for st, sd, n, req, fq in counts:
            by_status[st] += n
            requested[sd] += req
            filled[sd] += fq
        submitted = sum(n for s, n in by_status.items() if s != "intent")
        notional: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"buy": ZERO, "sell": ZERO})
        for bucket, side_, _n, value in buckets:
            notional[_bucket_label(bucket, w.granularity)][side_] += value or ZERO
        labels = bucket_keys(w.start, w.end, w.granularity)
        return out.OrdersReport(
            coverage=coverage,
            submitted=submitted,
            filled=by_status["filled"],
            partially_filled=by_status["partially_filled"],
            rejected=by_status["rejected"],
            cancelled=by_status["cancelled"],
            pending=sum(by_status[s] for s in OPEN_ORDER_STATUSES),
            order_to_fill_rate=pnl.rate(_orders_with_fill(by_status), submitted),
            buy_qty_requested=requested["buy"],
            sell_qty_requested=requested["sell"],
            buy_qty_filled=filled["buy"],
            sell_qty_filled=filled["sell"],
            executed_notional=fills.notional,
            fees=None,
            status_distribution=[
                out.LabelCount(key=s, label=s.replace("_", " ").capitalize(), count=n)
                for s, n in sorted(by_status.items(), key=lambda kv: -kv[1])
            ],
            notional_by_bucket=[
                out.BucketNotional(bucket=k, buy=notional[k]["buy"], sell=notional[k]["sell"])
                for k in labels
            ],
            by_symbol=_symbol_fills(by_symbol),
            broker_rejections=[
                out.ReasonCount(source="broker", reason=r, count=n) for r, n in broker
            ],
            rows=[_order_row(r, scope.mode_label) for r in rows],
            total_rows=total,
        )

    # --------------------------------------------------------------------------- pnl

    async def pnl(self, scope: Scope) -> out.PnlReport:
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        val = await self._valuation(f)
        async with self._sessions() as db:
            buckets = await queries.realized_buckets(db, f, w.start, w.end, w.granularity)
            closed = await queries.closed_trades(db, f, w.start, w.end)
            coverage = await self._coverage(db, scope, val.unpriced)
        series = _realized_series(buckets, w)
        realized = pnl.total(p.realized for p in series if p.has_activity)
        s = pnl.summarize([pnl.ClosedTrade(c.realized, c.closed_at - c.opened_at) for c in closed])
        return out.PnlReport(
            coverage=coverage,
            realized=realized,
            unrealized=val.unrealized,
            net=_add(realized, val.unrealized),
            gross_profit=s.gross_profit,
            gross_loss=s.gross_loss,
            fees=None,
            net_after_costs=None,
            wins=s.wins,
            losses=s.losses,
            breakeven=s.breakeven,
            unknown_outcomes=s.unknown,
            win_rate=s.win_rate,
            avg_win=s.avg_win,
            avg_loss=s.avg_loss,
            largest_win=s.largest_win,
            largest_loss=s.largest_loss,
            profit_factor=s.profit_factor,
            avg_hold_seconds=(
                Decimal(str(s.avg_hold.total_seconds())) if s.avg_hold is not None else None
            ),
            series=series,
        )

    # ----------------------------------------------------------------- instruments

    async def instruments(
        self, scope: Scope, *, sort: str, descending: bool, page: int, page_size: int
    ) -> out.InstrumentsReport:
        if sort not in INSTRUMENT_SORTS:
            raise ApiError(f"sort must be one of {sorted(INSTRUMENT_SORTS)}", "invalid", 422)
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        async with self._sessions() as db:
            lots = await queries.open_lots(db, f)
        val = await self._valuation(f, lots)
        async with self._sessions() as db:
            groups = await queries.signal_groups(db, f, w.start, w.end)
            orders = await queries.orders_by_symbol(db, f, w.start, w.end)
            fills = await queries.fills_by_symbol(db, f, w.start, w.end)
            realized = await queries.realized_buckets(db, f, w.start, w.end, None)
            closed = await queries.closed_trades(db, f, w.start, w.end)
            coverage = await self._coverage(db, scope, val.unpriced)
        rows = _instrument_rows(groups, orders, fills, realized, closed, lots, val)
        rows = _sorted(rows, sort, descending)
        start = (page - 1) * page_size
        return out.InstrumentsReport(
            coverage=coverage, rows=rows[start : start + page_size], total_rows=len(rows)
        )

    # ------------------------------------------------------------------------ trades

    async def trades(self, scope: Scope, q: queries.TradeQuery) -> out.TradesReport:
        scope = await self._resolve(scope)
        w = scope.window
        assert w.start is not None  # noqa: S101
        q = replace(q, start=w.start, end=w.end)
        async with self._sessions() as db:
            page, total = await queries.trades_page(db, scope.filters, q)
            rows = await self._trade_rows(db, page)
            coverage = await self._coverage(
                db, scope, sum(1 for r in rows if r.unrealized_pnl is None and r.open_qty)
            )
        return out.TradesReport(coverage=coverage, rows=rows, total_rows=total)

    async def _trade_rows(
        self, db: AsyncSession, data: list[queries.TradeData]
    ) -> list[out.TradeRow]:
        attribution = await queries.lot_attribution(db, {d.lot.lot_id for d in data})
        names = await queries.provider_names(
            db, {d.lot.provider_id for d in data if d.lot.provider_id is not None}
        )
        open_lots = [d.lot for d in data if d.lot.status == "open"]
        marks = await self._valuation(Filters(), open_lots) if open_lots else None
        return [
            _trade_row(
                d,
                marks.per_lot.get(d.lot.lot_id) if marks else None,
                attribution.get(d.lot.lot_id),
                names,
            )
            for d in data
        ]

    async def trade_detail(self, lot_id: uuid.UUID, scope: Scope) -> out.TradeDetail:
        scope = await self._resolve(scope)
        async with self._sessions() as db:
            page, _ = await queries.trades_page(
                db,
                Filters(),
                queries.TradeQuery(start=self._now, end=self._now, lot_id=lot_id, limit=1),
            )
            if not page:
                raise ApiError("trade not found", "not_found", 404)
            [trade] = await self._trade_rows(db, page)
            lot = page[0].lot
            orders = list(
                (
                    await db.scalars(
                        select(Order)
                        .where(col(Order.lot_id) == lot_id)
                        .order_by(col(Order.created_at))
                    )
                ).all()
            )
            order_ids = [o.order_id for o in orders]
            fills = list(
                (
                    await db.scalars(
                        select(Fill)
                        .where(col(Fill.order_id).in_(order_ids))
                        .order_by(col(Fill.filled_at))
                    )
                ).all()
            )
            events = (
                await db.scalars(
                    select(OrderEvent)
                    .where(col(OrderEvent.order_id).in_(order_ids))
                    .order_by(col(OrderEvent.id))
                )
            ).all()
            lot_events = (
                await db.scalars(
                    select(LotEvent)
                    .where(col(LotEvent.lot_id) == lot_id)
                    .order_by(col(LotEvent.id))
                )
            ).all()
            stops = await queries.stops_for(db, lot_id)
            card_ids = {o.card_id for o in orders if o.card_id}
            cards = list(
                (await db.scalars(select(Card).where(col(Card.card_id).in_(card_ids)))).all()
            )
            approvals = {
                a.card_id: a
                for a in (
                    await db.scalars(select(Approval).where(col(Approval.card_id).in_(card_ids)))
                ).all()
            }
            card_events = (
                await db.execute(
                    select(col(CardEvent.card_id), col(CardEvent.reason_code)).where(
                        col(CardEvent.card_id).in_(card_ids)
                    )
                )
            ).all()
            signals = await self._trade_signals(db, {c.signal_id for c in cards if c.signal_id})
            audit_ids = {str(i) for i in (*card_ids, *order_ids, lot_id)}
            audit = (
                await db.scalars(
                    select(AuditLog)
                    .where(col(AuditLog.resource_id).in_(audit_ids))
                    .order_by(col(AuditLog.at))
                )
            ).all()
            coverage = await self._coverage(
                db, scope, 1 if trade.open_qty and trade.unrealized_pnl is None else 0
            )
        side_of = {o.order_id: str(o.side) for o in orders}
        return out.TradeDetail(
            coverage=coverage,
            trade=trade,
            signals=signals,
            decisions=[_decision(c, approvals.get(c.card_id), card_events) for c in cards],
            orders=[
                _trade_order(o, [e for e in events if e.order_id == o.order_id]) for o in orders
            ],
            fills=[
                out.TradeFill(
                    fill_id=x.fill_id,
                    order_id=x.order_id,
                    side=side_of[x.order_id],
                    qty=x.qty,
                    price=x.price,
                    filled_at=x.filled_at,
                    fee=None,
                )
                for x in fills
            ],
            lot_history=[
                out.LotStep(
                    change=str(e.change),
                    qty_before=e.qty_before,
                    qty_after=e.qty_after,
                    avg_before=e.avg_before,
                    avg_after=e.avg_after,
                    fill_id=e.fill_id,
                    at=e.at,
                )
                for e in lot_events
            ],
            stops=[
                out.TradeStop(
                    kind=str(s.kind),
                    level=s.level,
                    qty=s.qty,
                    status=str(s.status),
                    held_by=str(s.held_by),
                )
                for s in stops
            ],
            audit=[
                out.TradeAuditEntry(
                    at=a.at,
                    action=str(a.action),
                    user_email=a.user_email,
                    resource_type=a.resource_type,
                    resource_id=a.resource_id,
                )
                for a in audit
            ],
            outcome_explanation=_explain(trade, lot, len(fills)),
        )

    async def _trade_signals(self, db: AsyncSession, ids: set[uuid.UUID]) -> list[out.TradeSignal]:
        if not ids:
            return []
        sigs = (await db.scalars(select(Signal).where(col(Signal.signal_id).in_(ids)))).all()
        names = await queries.provider_names(db, {s.provider_id for s in sigs})
        result = []
        for s in sigs:
            msg = await db.get(MessageEvent, (s.message_id, s.event_type, s.event_seq))
            result.append(
                out.TradeSignal(
                    signal_id=s.signal_id,
                    msg_type=str(s.msg_type),
                    interpreter=str(s.interpreter),
                    posted_at=msg.posted_at if msg else s.created_at,
                    text=msg.text if msg else None,
                    provider_id=s.provider_id,
                    provider_name=names.get(s.provider_id),
                    parsed=s.fields,
                )
            )
        return sorted(result, key=lambda x: x.posted_at)

    # -------------------------------------------------------------------- timeseries

    async def timeseries(self, scope: Scope, metrics: list[str]) -> out.TimeseriesReport:
        unknown = [m for m in metrics if m not in TIMESERIES_METRICS]
        if unknown:
            raise ApiError(
                f"unknown metric {unknown[0]}; use {list(TIMESERIES_METRICS)}", "invalid", 422
            )
        metrics = metrics or list(TIMESERIES_METRICS)
        scope = await self._resolve(scope)
        f, w = scope.filters, scope.window
        assert w.start is not None  # noqa: S101
        gran = w.granularity
        async with self._sessions() as db:
            realized = await queries.realized_buckets(db, f, w.start, w.end, gran)
            fills = await queries.fill_buckets(db, f, w.start, w.end, gran)
            closed = await queries.closed_trades(db, f, w.start, w.end)
            sig = await queries.signal_buckets(db, f, w.start, w.end, gran)
            coverage = await self._coverage(db, scope, 0)
        realized_series = _realized_series(realized, w)
        cum = {p.bucket: p.cumulative for p in realized_series}
        pnl_by = {p.bucket: p.realized for p in realized_series}
        active: set[str] = {p.bucket for p in realized_series if p.has_activity}
        fills_by: dict[tuple[str, str], tuple[int, Decimal]] = {
            (_bucket_label(b, gran), s): (n, v or ZERO) for b, s, n, v in fills
        }
        active |= {k for k, _ in fills_by}
        outcome_by: dict[str, Counter[str]] = defaultdict(Counter)
        for c in closed:
            key = bucket_key(floor_et(c.closed_at, gran), gran)
            outcome_by[key][pnl.outcome(True, c.realized).value] += 1
        active |= set(outcome_by)
        sig_by = {_bucket_label(b, gran): (t, a, r) for b, t, a, r in sig}
        active |= set(sig_by)
        points = []
        for key in bucket_keys(w.start, w.end, gran):
            buy, sell = (
                fills_by.get((key, "buy"), (0, ZERO)),
                fills_by.get((key, "sell"), (0, ZERO)),
            )
            tot, acc, rej = sig_by.get(key, (0, 0, 0))
            oc = outcome_by.get(key, Counter())
            values: dict[str, Decimal | None] = {
                "realized_pnl": pnl_by.get(key, ZERO),
                "cumulative_pnl": cum.get(key),
                "fills_buy": Decimal(buy[0]),
                "fills_sell": Decimal(sell[0]),
                "notional_buy": buy[1],
                "notional_sell": sell[1],
                "wins": Decimal(oc["profit"]),
                "losses": Decimal(oc["loss"]),
                "breakeven": Decimal(oc["breakeven"]),
                "signals": Decimal(tot),
                "accepted_signals": Decimal(acc),
                "rejected_signals": Decimal(rej),
            }
            points.append(
                out.SeriesPoint(
                    bucket=key,
                    has_activity=key in active,
                    values={m: values[m] for m in metrics},
                )
            )
        return out.TimeseriesReport(
            coverage=coverage, granularity=gran.value, metrics=metrics, points=points
        )


# ------------------------------------------------------------------------- pure helpers


@dataclass(frozen=True)
class Counts:
    total: int
    parsed: int
    actionable: int
    accepted: int
    rejected: int
    no_card: int
    approved: int
    user_rejected: int
    pending: int
    lapsed: int
    ordered: int
    filled: int


def _counts(groups: list[SignalGroup]) -> Counts:
    def n(pred: Any) -> int:
        return sum(g.count for g in groups if pred(g))

    return Counts(
        total=n(lambda g: True),
        parsed=n(lambda g: g.parsed),
        actionable=n(lambda g: g.actionable),
        accepted=n(lambda g: g.accepted),
        rejected=n(lambda g: not g.accepted and g.disabled),
        no_card=n(lambda g: g.actionable and not g.has_card),
        approved=n(lambda g: g.approved),
        user_rejected=n(lambda g: g.user_rejected and not g.approved),
        pending=n(lambda g: g.pending and not g.approved),
        lapsed=n(lambda g: g.lapsed and not g.approved and not g.user_rejected),
        ordered=n(lambda g: g.ordered),
        filled=n(lambda g: g.filled),
    )


def _signal_reasons(groups: list[SignalGroup]) -> list[out.ReasonCount]:
    tally: Counter[tuple[str, str]] = Counter()
    for g in groups:
        if not g.accepted and g.disabled:
            tally["decision_engine", g.reason or "no reason recorded"] += g.count
        if g.user_rejected and not g.approved:
            tally["user", "rejected by user"] += g.count
        if g.lapsed and not g.approved and not g.user_rejected:
            tally["card_lifecycle", g.lapse_reason or "expired or invalidated"] += g.count
    return [
        out.ReasonCount(source=s, reason=r, count=n)
        for (s, r), n in sorted(tally.items(), key=lambda kv: -kv[1])
    ]


def _by_symbol(groups: list[SignalGroup]) -> list[out.SymbolSignals]:
    acc: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in groups:
        if not g.actionable:
            continue
        row = acc[g.symbol.upper() if g.symbol else None]
        row[0] += g.count
        row[1] += g.count if g.accepted else 0
        row[2] += g.count if not g.accepted and g.disabled else 0
    top = sorted(acc.items(), key=lambda kv: -kv[1][0])[:25]
    return [out.SymbolSignals(symbol=s, total=v[0], accepted=v[1], rejected=v[2]) for s, v in top]


def _by_provider(groups: list[SignalGroup], names: dict[int, str]) -> list[out.ProviderSignals]:
    acc: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in groups:
        if not g.actionable:
            continue
        row = acc[g.provider_id]
        row[0] += g.count
        row[1] += g.count if g.accepted else 0
        row[2] += g.count if not g.accepted and g.disabled else 0
    return [
        out.ProviderSignals(
            provider_id=p, name=names.get(p), total=v[0], accepted=v[1], rejected=v[2]
        )
        for p, v in sorted(acc.items(), key=lambda kv: -kv[1][0])
    ]


def _signal_row(r: queries.SignalRowData) -> out.SignalRow:
    if r.accepted:
        engine = "accepted"
    elif r.disabled:
        engine = "rejected"
    else:
        engine = "no_card"
    if r.approved:
        approval = "approved"
    elif r.user_rejected:
        approval = "rejected"
    elif r.pending:
        approval = "pending"
    elif r.lapsed:
        approval = "lapsed"
    else:
        approval = "not_applicable"
    return out.SignalRow(
        signal_id=r.signal_id,
        at=r.at,
        provider_id=r.provider_id,
        symbol=r.symbol,
        msg_type=r.msg_type,
        engine_status=engine,
        approval_status=approval,
        execution_status="filled" if r.filled else "ordered" if r.ordered else "none",
        reason=r.reason or r.lapse_reason,
    )


def _single(
    attribution: queries.LotAttribution | None, signal_id: uuid.UUID, foreign: bool
) -> bool:
    """The lot came from this one signal alone (no adds from others, no manual entry)."""
    return (
        attribution is not None
        and not foreign
        and attribution.signal_ids == {signal_id}
        and attribution.unattributed_orders == 0
    )


def _add(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    return None if a is None or b is None else a + b


def _bucket_label(raw: str, gran: Granularity) -> str:
    """A database bucket timestamp ('2026-09-28 10:00:00') in the report's key format."""
    stamp = datetime.fromisoformat(raw)
    return bucket_key(stamp, gran)


def _realized_series(
    rows: list[tuple[str | None, str, str, Decimal | None, int]], w: Window
) -> list[out.PnlPoint]:
    assert w.start is not None  # noqa: S101
    gran = w.granularity
    per: dict[str, list[Decimal | None]] = defaultdict(list)
    for bucket, _u, _i, value, _n in rows:
        if bucket is not None:
            per[_bucket_label(bucket, gran)].append(value)
    keys = bucket_keys(w.start, w.end, gran)
    totals = {k: pnl.total(per[k]) if k in per else ZERO for k in keys}
    now = datetime(2000, 1, 1, tzinfo=UTC)
    cumulative = pnl.cumulative(
        [(now + timedelta(seconds=i), totals[k]) for i, k in enumerate(keys)]
    )
    return [
        out.PnlPoint(bucket=k, realized=totals[k], cumulative=c, has_activity=k in per)
        for k, c in zip(keys, cumulative, strict=True)
    ]


def _orders_with_fill(by_status: Counter[str]) -> int:
    return by_status["filled"] + by_status["partially_filled"]


def _symbol_fills(
    rows: list[tuple[str, str, str, int, int, Decimal, datetime]],
) -> list[out.SymbolFills]:
    acc: dict[tuple[str, str], dict[str, list[Decimal]]] = defaultdict(
        lambda: {"buy": [ZERO, ZERO], "sell": [ZERO, ZERO]}
    )
    for symbol, instrument, side, _n, qty, value, _last in rows:
        key = (queries.underlying_of(symbol), instrument)
        acc[key][side][0] += qty
        acc[key][side][1] += value
    result = []
    for (symbol, instrument), sides in acc.items():
        buy, sell = sides["buy"], sides["sell"]
        result.append(
            out.SymbolFills(
                symbol=symbol,
                instrument=instrument,
                buy_qty=int(buy[0]),
                sell_qty=int(sell[0]),
                avg_buy_price=buy[1] / buy[0] if buy[0] else None,
                avg_sell_price=sell[1] / sell[0] if sell[0] else None,
            )
        )
    return sorted(result, key=lambda r: -(r.buy_qty + r.sell_qty))[:30]


def _order_row(r: queries.OrderRowData, mode_label: str) -> out.OrderRow:
    o = r.order
    mult = pnl.multiplier(o.instrument)
    filled = o.filled_qty > 0 and o.avg_fill_price is not None
    return out.OrderRow(
        order_id=o.order_id,
        at=o.created_at,
        symbol=o.broker_symbol,
        instrument=str(o.instrument),
        side=str(o.side),
        kind=str(o.kind),
        status=str(o.status),
        mode=str(o.mode),
        requested_qty=o.qty,
        filled_qty=o.filled_qty,
        avg_fill_price=o.avg_fill_price if filled else None,
        executed_notional=(
            o.avg_fill_price * o.filled_qty * mult if filled and o.avg_fill_price else None
        ),
        fees=None,
        broker=f"{o.mode!s} broker",
        broker_order_id=o.broker_order_id,
        signal_id=r.signal_id,
        lot_id=o.lot_id,
    )


def _instrument_rows(
    groups: list[SignalGroup],
    orders: list[tuple[str, str, str, int]],
    fills: list[tuple[str, str, str, int, int, Decimal, datetime]],
    realized: list[tuple[str | None, str, str, Decimal | None, int]],
    closed: list[queries.ClosedTrade],
    lots: list[Lot],
    val: Valuation,
) -> list[out.InstrumentRow]:
    keys: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: defaultdict(lambda: 0))

    def acc(symbol: str, instrument: str) -> dict[str, Any]:
        return keys[(symbol.upper(), instrument)]

    for g in groups:
        if g.symbol and g.actionable:
            a = acc(g.symbol, g.instrument or "unclear")
            a["signals"] += g.count
            a["accepted"] += g.count if g.accepted else 0
            a["rejected"] += g.count if not g.accepted and g.disabled else 0
    for symbol, ins, side, n in orders:
        acc(queries.underlying_of(symbol), ins)[f"{side}_orders"] += n
    for symbol, ins, side, _n, qty, value, last in fills:
        a = acc(queries.underlying_of(symbol), ins)
        a[f"{side}_qty"] += qty
        a[f"{side}_value"] = a.get(f"{side}_value", ZERO) + value
        a["last"] = max(a["last"], last) if a.get("last") else last
    realized_by: dict[tuple[str, str], list[Decimal | None]] = defaultdict(list)
    for _b, u, i, real_value, _n in realized:
        realized_by[(u.upper(), i)].append(real_value)
        acc(u, i)
    for c in closed:
        a = acc(c.underlying, c.instrument)
        a.setdefault("closed", []).append(pnl.ClosedTrade(c.realized, None))
    unrealized_by: dict[tuple[str, str], list[Decimal | None]] = defaultdict(list)
    for lot in lots:
        a = acc(lot.underlying, str(lot.instrument))
        a["open_qty"] += lot.qty
        unrealized_by[(lot.underlying.upper(), str(lot.instrument))].append(
            val.per_lot.get(lot.lot_id)
        )
    rows = []
    for (symbol, ins), a in keys.items():
        trades = a.get("closed", [])
        s = pnl.summarize(trades)
        real = pnl.total(realized_by[(symbol, ins)]) if (symbol, ins) in realized_by else ZERO
        unreal = pnl.total(unrealized_by[(symbol, ins)]) if (symbol, ins) in unrealized_by else ZERO
        rows.append(
            out.InstrumentRow(
                symbol=symbol,
                instrument=ins,
                signals=a["signals"],
                accepted_signals=a["accepted"],
                rejected_signals=a["rejected"],
                buy_orders=a["buy_orders"],
                sell_orders=a["sell_orders"],
                buy_qty=int(a["buy_qty"]),
                sell_qty=int(a["sell_qty"]),
                open_qty=a["open_qty"],
                avg_entry_price=a["buy_value"] / a["buy_qty"] if a.get("buy_qty") else None,
                avg_exit_price=a["sell_value"] / a["sell_qty"] if a.get("sell_qty") else None,
                realized_pnl=real,
                unrealized_pnl=unreal,
                net_pnl=_add(real, unreal),
                fees=None,
                wins=s.wins,
                losses=s.losses,
                closed_trades=len(trades),
                win_rate=s.win_rate,
                low_sample=len(trades) < pnl.LOW_SAMPLE_TRADES,
                last_activity=a.get("last"),
            )
        )
    return rows


def _sorted(rows: list[out.InstrumentRow], sort: str, descending: bool) -> list[out.InstrumentRow]:
    def value(r: out.InstrumentRow) -> Any:
        return r.symbol if sort == "symbol" else getattr(r, sort)

    known = [r for r in rows if value(r) is not None]
    unknown = [r for r in rows if value(r) is None]
    return sorted(known, key=value, reverse=descending) + unknown


def _contract(lot: Lot) -> str | None:
    if lot.instrument != "option" or lot.strike is None or lot.expiry is None:
        return None
    right = "C" if lot.option_right == "call" else "P"
    return f"{lot.underlying} {lot.strike.normalize():f}{right} {lot.expiry.isoformat()}"


def _trade_row(
    d: queries.TradeData,
    unrealized: Decimal | None,
    attribution: queries.LotAttribution | None,
    names: dict[int, str],
) -> out.TradeRow:
    lot = d.lot
    mult = pnl.multiplier(lot.instrument)
    closed = lot.status == "closed"
    realized = d.realized if d.sold_qty else (None if closed else ZERO)
    live = None if closed else unrealized
    net = realized if closed else _add(realized, live)
    signals = attribution.signal_ids if attribution else frozenset()
    if not signals:
        kind = "none"
    elif len(signals) == 1 and attribution and attribution.unattributed_orders == 0:
        kind = "single"
    else:
        kind = "shared"
    return out.TradeRow(
        lot_id=lot.lot_id,
        symbol=lot.broker_symbol,
        underlying=lot.underlying,
        instrument=str(lot.instrument),
        contract=_contract(lot),
        status=str(lot.status),
        outcome=pnl.outcome(closed, realized).value,
        opened_at=lot.opened_at,
        closed_at=lot.closed_at,
        requested_qty=d.requested_qty,
        entry_qty=d.entry_qty,
        exit_qty=d.sold_qty,
        open_qty=lot.qty,
        avg_entry_price=(d.entry_value / d.entry_qty)
        if d.entry_qty and d.entry_value
        else lot.avg_price,
        avg_exit_price=(d.sold_value / d.sold_qty) if d.sold_qty and d.sold_value else None,
        entry_cost=d.entry_value * mult if d.entry_value is not None and d.entry_qty else None,
        realized_pnl=realized,
        unrealized_pnl=live,
        net_pnl=net,
        fees=None,
        hold_seconds=int((lot.closed_at - lot.opened_at).total_seconds())
        if lot.closed_at
        else None,
        mode=d.mode,
        provider_id=lot.provider_id,
        provider_name=names.get(lot.provider_id) if lot.provider_id is not None else None,
        signal_id=next(iter(signals)) if kind == "single" else None,
        attribution=kind,
        is_foreign=lot.is_foreign,
    )


def _decision(c: Card, a: Approval | None, events: Any) -> out.TradeDecision:
    proposal = c.proposal or {}
    approved = (a.approved_proposal or {}) if a else {}
    warnings = [
        w if isinstance(w, str) else str(w.get("code") or w.get("text") or w)
        for w in (proposal.get("warnings") or [])
    ]
    tally = Counter(str(r) for cid, r in events if cid == c.card_id)
    return out.TradeDecision(
        card_id=c.card_id,
        action=str(c.action),
        card_type=str(c.card_type),
        state=str(c.state),
        disabled_reason=c.disabled_reason,
        warnings=warnings,
        proposed_qty=proposal.get("qty"),
        events=[
            out.LabelCount(key=k, label=k.replace("_", " "), count=n) for k, n in tally.items()
        ],
        decided=str(a.decision) if a else None,
        decided_at=a.decided_at if a else None,
        approved_qty=approved.get("qty"),
        approved_limit_price=_dec(approved.get("limit_price")),
        edited=bool(a and a.edits),
    )


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _trade_order(o: Order, events: list[OrderEvent]) -> out.TradeOrder:
    return out.TradeOrder(
        order_id=o.order_id,
        kind=str(o.kind),
        side=str(o.side),
        order_type=str(o.order_type),
        status=str(o.status),
        mode=str(o.mode),
        requested_qty=o.qty,
        filled_qty=o.filled_qty,
        remaining_qty=o.qty - o.filled_qty,
        limit_price=o.limit_price,
        stop_price=o.stop_price,
        broker_order_id=o.broker_order_id,
        client_order_id=o.client_order_id,
        auto_reason=str(o.auto_reason) if o.auto_reason else None,
        submitted_at=o.created_at,
        steps=[
            out.OrderStep(
                status=str(e.status),
                source=str(e.source),
                at=e.at,
                broker_status=(e.broker_payload or {}).get("broker_status"),
            )
            for e in events
        ],
    )


def _usd(v: Decimal | None) -> str:
    return "unknown" if v is None else f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def _px(v: Decimal | None) -> str:
    """A price without trailing zeros, but never fewer than two decimals."""
    if v is None:
        return "unknown"
    text = f"{v:.4f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _explain(t: out.TradeRow, lot: Lot, fills: int) -> str:
    """Which executions support the outcome, in words. Never a verdict on an open trade."""
    if t.outcome == "open":
        part = (
            f"; {t.exit_qty} already sold for {_usd(t.realized_pnl)} realized"
            if t.exit_qty and t.realized_pnl is not None
            else ""
        )
        return (
            f"Open, not final: {t.open_qty} still held{part}. "
            "Current unrealized P&L can still change."
        )
    if t.outcome == "unknown":
        return (
            "Closed, but the result is unknown: "
            + (
                "no average cost is recorded"
                if lot.avg_price is None
                else "a sell has no cost basis"
            )
            + f" (fills: {fills})."
        )
    return (
        f"Closed. {t.exit_qty} sold at an average {_px(t.avg_exit_price)} against an average "
        f"cost of {_px(t.avg_entry_price)}: realized {_usd(t.realized_pnl)}, before fees "
        "(not captured)."
    )
