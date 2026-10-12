import { useState } from "react"
import { keepPreviousData, queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import { isReady, toParams, type ReportFilters } from "@/features/reports/filters"
import type {
  InstrumentsReport,
  Overview,
  OrdersReport,
  PnlReport,
  SignalsReport,
  TradeDetail,
  TradesReport,
} from "@/features/reports/types"

const BASE = "/api/v1/reports/trading"

/** Every key sits under `["reports"]`: the Refresh button invalidates the whole report at once. */
export const reportKeys = {
  all: ["reports"] as const,
  section: (name: string, params: object) => ["reports", name, params] as const,
}

export type TableParams = { page: number; pageSize?: number }
export type TradeParams = TableParams & {
  state: "" | "open" | "closed"
  outcome: "" | "profit" | "loss" | "breakeven" | "open" | "unknown"
  search: string
  sort: "opened_at" | "closed_at" | "realized" | "cost" | "symbol"
  order: "asc" | "desc"
}
export type InstrumentParams = TableParams & {
  sort: "net_pnl" | "realized_pnl" | "win_rate" | "closed_trades" | "buy_qty" | "symbol"
  order: "asc" | "desc"
}
export type SignalParams = { page: number; engine: "" | "accepted" | "rejected" | "no_card" }
export type OrderParams = TableParams & { side: "" | "buy" | "sell"; status: string }

/** Table state for one report section. A new global filter set starts back at page 1. */
export function useSectionParams<T extends { page: number }>(filters: ReportFilters, initial: T) {
  const [params, setParams] = useState(initial)
  const scope = [
    filters.period,
    filters.startDate,
    filters.endDate,
    filters.mode,
    filters.symbol,
    filters.instrument,
  ].join("\0")
  const [applied, setApplied] = useState(scope)
  if (applied !== scope) {
    setApplied(scope)
    if (params.page !== 1) setParams({ ...params, page: 1 })
  }
  return [params, setParams] as const
}

/** Previous data stays on screen while a new period loads, so charts do not flash empty. */
function section<T>(name: string, f: ReportFilters, extra: object, path: string) {
  const params = { ...toParams(f), ...extra }
  return queryOptions({
    queryKey: reportKeys.section(name, params),
    queryFn: () => apiGet<T>(`${BASE}/${path}`, params),
    staleTime: 30_000,
    enabled: isReady(f),
    placeholderData: keepPreviousData,
  })
}

export const useOverview = (f: ReportFilters) => useQuery(section<Overview>("overview", f, {}, "overview"))
export const usePnl = (f: ReportFilters) => useQuery(section<PnlReport>("pnl", f, {}, "pnl"))
export const useSignals = (f: ReportFilters, p: SignalParams) =>
  useQuery(section<SignalsReport>("signals", f, p, "signals"))
export const useOrders = (f: ReportFilters, p: OrderParams) =>
  useQuery(section<OrdersReport>("orders", f, p, "orders"))
export const useInstruments = (f: ReportFilters, p: InstrumentParams) =>
  useQuery(section<InstrumentsReport>("instruments", f, p, "instruments"))
export const useTrades = (f: ReportFilters, p: TradeParams) =>
  useQuery(section<TradesReport>("trades", f, p, "trades"))

export function useTradeDetail(f: ReportFilters, lotId: string | null) {
  const params = toParams(f)
  return useQuery({
    queryKey: reportKeys.section("trade", { ...params, lotId }),
    queryFn: () => apiGet<TradeDetail>(`${BASE}/trades/${lotId}`, params),
    staleTime: 30_000,
    enabled: lotId != null && isReady(f),
  })
}
