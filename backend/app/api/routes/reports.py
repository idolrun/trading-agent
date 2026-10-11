"""Trading reports (F083): read-only GET endpoints over signals, cards, orders, fills and lots."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import ClockDep, ConfigServiceDep, SessionDep, SessionFactoryDep
from app.api.schemas import reports as out
from app.ledger.models import Instrument
from app.reports.period import Period, resolve
from app.reports.queries import Filters, TradeQuery
from app.reports.service import ReportService, Scope

router = APIRouter(prefix="/reports/trading", tags=["reports"])

ModeParam = Literal["shadow", "paper", "live", "all"]
Symbol = Annotated[str | None, Query(pattern=r"^[A-Za-z][A-Za-z.\-]{0,9}$")]
Page = Annotated[int, Query(ge=1, le=10_000)]
PageSize = Annotated[int, Query(alias="pageSize", ge=1, le=100)]
Order = Literal["asc", "desc"]


@dataclass(frozen=True)
class Report:
    service: ReportService
    scope: Scope


def report(
    request: Request,
    sessions: SessionFactoryDep,
    config: ConfigServiceDep,
    clock: ClockDep,
    _ctx: SessionDep,
    period: Period = Period.D7,
    start_date: Annotated[date | None, Query(alias="startDate")] = None,
    end_date: Annotated[date | None, Query(alias="endDate")] = None,
    mode: ModeParam | None = None,
    symbol: Symbol = None,
    instrument: Instrument | None = None,
    provider_id: Annotated[int | None, Query(alias="providerId", gt=0)] = None,
) -> Report:
    """Shared filters. No `mode` = the mode the app is trading in now, so simulated and real
    results never mix by accident; `mode=all` mixes them on request."""
    now = clock.now()
    cfg = config.get()
    chosen = mode or (cfg.trading_mode.value if cfg else "shadow")
    scope = Scope(
        window=resolve(period, start_date, end_date, now),
        filters=Filters(
            mode=None if chosen == "all" else chosen,
            symbol=symbol,
            instrument=instrument.value if instrument else None,
            provider_id=provider_id,
        ),
        mode_label=chosen,
    )
    service = ReportService(
        sessions,
        request.app.state.marketdata,
        cfg.quote_max_age_s if cfg else None,
        now,
    )
    return Report(service, scope)


ReportDep = Annotated[Report, Depends(report)]


@router.get("/overview")
async def overview(r: ReportDep) -> out.Overview:
    return await r.service.overview(r.scope)


@router.get("/signals")
async def signals(
    r: ReportDep,
    engine: Literal["accepted", "rejected", "no_card"] | None = None,
    page: Page = 1,
) -> out.SignalsReport:
    return await r.service.signals(r.scope, engine=engine, page=page)


@router.get("/orders")
async def orders(
    r: ReportDep,
    side: Literal["buy", "sell"] | None = None,
    status: Literal[
        "intent",
        "submitted",
        "working",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "unknown",
    ]
    | None = None,
    page: Page = 1,
    page_size: PageSize = 25,
) -> out.OrdersReport:
    return await r.service.orders(r.scope, side=side, status=status, page=page, page_size=page_size)


@router.get("/pnl")
async def pnl(r: ReportDep) -> out.PnlReport:
    return await r.service.pnl(r.scope)


@router.get("/instruments")
async def instruments(
    r: ReportDep,
    sort: str = "net_pnl",
    order: Order = "desc",
    page: Page = 1,
    page_size: PageSize = 25,
) -> out.InstrumentsReport:
    return await r.service.instruments(
        r.scope, sort=sort, descending=order == "desc", page=page, page_size=page_size
    )


@router.get("/trades")
async def trades(
    r: ReportDep,
    state: Literal["open", "closed"] | None = None,
    outcome: Literal["profit", "loss", "breakeven", "open", "unknown"] | None = None,
    search: Annotated[str | None, Query(max_length=64)] = None,
    sort: Literal["opened_at", "closed_at", "realized", "cost", "symbol"] = "opened_at",
    order: Order = "desc",
    page: Page = 1,
    page_size: PageSize = 25,
) -> out.TradesReport:
    query = TradeQuery(
        start=r.scope.window.end,  # replaced by the resolved window in the service
        end=r.scope.window.end,
        state=state,
        outcome=outcome,
        search=search,
        sort=sort,
        descending=order == "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return await r.service.trades(r.scope, query)


@router.get("/trades/{trade_id}")
async def trade_detail(trade_id: uuid.UUID, r: ReportDep) -> out.TradeDetail:
    return await r.service.trade_detail(trade_id, r.scope)


@router.get("/timeseries")
async def timeseries(
    r: ReportDep, metrics: Annotated[str | None, Query(max_length=300)] = None
) -> out.TimeseriesReport:
    """`metrics`: comma-separated names (see `TIMESERIES_METRICS`); none = all of them."""
    names = [m.strip() for m in (metrics or "").split(",") if m.strip()]
    return await r.service.timeseries(r.scope, names)
