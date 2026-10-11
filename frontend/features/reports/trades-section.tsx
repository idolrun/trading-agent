"use client"

import { useState } from "react"
import { QueryState } from "@/components/query-state"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { Table, TableBody, TableCell, TableHeader, TableRow, TableHead } from "@/components/ui/table"
import { CoverageNote } from "@/features/reports/coverage-note"
import type { ReportFilters } from "@/features/reports/filters"
import { duration, money, price, stamp } from "@/features/reports/format"
import { useTrades, type TradeParams } from "@/features/reports/queries"
import { TradeSheet } from "@/features/reports/trade-sheet"
import { Notice, OutcomeBadge, Pager, Pnl, Section, SortHead, StatusBadge, Unavailable } from "@/features/reports/ui"

const PAGE_SIZE = 25
const DEFAULTS: TradeParams = { page: 1, state: "", outcome: "", search: "", sort: "opened_at", order: "desc" }

export function TradesSection({ filters }: { filters: ReportFilters }) {
  const [params, setParams] = useState<TradeParams>(DEFAULTS)
  const [typed, setTyped] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const query = useTrades(filters, { ...params, pageSize: PAGE_SIZE })
  const patch = (p: Partial<TradeParams>) => setParams({ ...params, page: 1, ...p })
  const sort = (key: TradeParams["sort"]) =>
    patch({ sort: key, order: params.sort === key && params.order === "desc" ? "asc" : "desc" })
  const head = (key: TradeParams["sort"], label: string, numeric = false) => (
    <SortHead label={label} active={params.sort === key} order={params.order} onSort={() => sort(key)} numeric={numeric} />
  )
  return (
    <>
      <Section
        title="Trade history"
        description="One row per position (lot): its entry fills, its exits and the result."
        actions={
          <form
            className="flex flex-wrap items-center gap-3 text-xs"
            role="search"
            onSubmit={(e) => {
              e.preventDefault()
              patch({ search: typed.trim() })
            }}
          >
            <label className="flex items-center gap-2">
              Search
              <Input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder="Symbol, signal or trade id"
                className="h-8 w-52"
                aria-label="Search trades by symbol, signal id or trade id"
              />
            </label>
            <label className="flex items-center gap-2">
              Status
              <NativeSelect size="sm" value={params.state} onChange={(e) => patch({ state: e.target.value as TradeParams["state"] })}>
                <NativeSelectOption value="">Open and closed</NativeSelectOption>
                <NativeSelectOption value="open">Open</NativeSelectOption>
                <NativeSelectOption value="closed">Closed</NativeSelectOption>
              </NativeSelect>
            </label>
            <label className="flex items-center gap-2">
              Result
              <NativeSelect size="sm" value={params.outcome} onChange={(e) => patch({ outcome: e.target.value as TradeParams["outcome"] })}>
                <NativeSelectOption value="">Any result</NativeSelectOption>
                <NativeSelectOption value="profit">Profit</NativeSelectOption>
                <NativeSelectOption value="loss">Loss</NativeSelectOption>
                <NativeSelectOption value="breakeven">Break-even</NativeSelectOption>
                <NativeSelectOption value="open">Open (not final)</NativeSelectOption>
                <NativeSelectOption value="unknown">Unknown</NativeSelectOption>
              </NativeSelect>
            </label>
          </form>
        }
      >
        <Notice>
          A BUY that has not been sold is an open position, not a completed trade. Only closed trades have a final result.
          Signals that were rejected, never approved or never filled have no position, so they are in the Signals and
          Execution tabs.
        </Notice>
        <QueryState
          query={query}
          loadingLabel="Loading trades"
          emptyTitle="No trades match"
          emptyDescription="A trade is a position with at least one fill. Change the period or filters, or wait for the first fill."
          isEmpty={(r) => r.totalRows === 0}
        >
          {(r) => (
            <div className="flex flex-col gap-3">
              <Table aria-label="Trade history">
                <TableHeader>
                  <TableRow>
                    {head("opened_at", "Opened")}
                    {head("symbol", "Symbol")}
                    <TableHead>Status</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead className="text-right">Qty (bought / sold / open)</TableHead>
                    <TableHead className="text-right">Requested</TableHead>
                    <TableHead className="text-right">Avg entry</TableHead>
                    <TableHead className="text-right">Avg exit</TableHead>
                    {head("cost", "Spent", true)}
                    {head("realized", "Realized", true)}
                    <TableHead className="text-right">Unrealized</TableHead>
                    <TableHead className="text-right">Fees</TableHead>
                    <TableHead className="text-right">Net</TableHead>
                    <TableHead className="text-right">Held</TableHead>
                    <TableHead>
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.rows.map((t) => (
                    <TableRow key={t.lotId}>
                      <TableCell>{stamp(t.openedAt)}</TableCell>
                      <TableCell>
                        <div className="font-mono font-medium">{t.underlying}</div>
                        <div className="text-xs text-muted-foreground">{t.contract ?? t.instrument}</div>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={t.status} />
                      </TableCell>
                      <TableCell>
                        <OutcomeBadge outcome={t.outcome} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {t.entryQty} / {t.exitQty} / {t.openQty}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{t.requestedQty ?? <Unavailable />}</TableCell>
                      <TableCell className="text-right tabular-nums">{price(t.avgEntryPrice)}</TableCell>
                      <TableCell className="text-right tabular-nums">{price(t.avgExitPrice)}</TableCell>
                      <TableCell className="text-right tabular-nums">{money(t.entryCost)}</TableCell>
                      <TableCell className="text-right">
                        <Pnl value={t.realizedPnl} />
                      </TableCell>
                      <TableCell className="text-right">
                        {t.status === "open" ? <Pnl value={t.unrealizedPnl} /> : <span className="text-muted-foreground">n/a · closed</span>}
                      </TableCell>
                      <TableCell className="text-right">
                        <Unavailable reason="not captured" />
                      </TableCell>
                      <TableCell className="text-right">
                        <Pnl value={t.netPnl} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{t.holdSeconds == null ? <Unavailable /> : duration(t.holdSeconds)}</TableCell>
                      <TableCell>
                        <button
                          type="button"
                          onClick={() => setOpen(t.lotId)}
                          className="rounded-sm text-xs font-medium underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                          aria-label={`Inspect ${t.underlying} trade opened ${stamp(t.openedAt)}`}
                        >
                          Inspect
                        </button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager page={params.page} pageSize={PAGE_SIZE} total={r.totalRows} onPage={(page) => setParams({ ...params, page })} />
              <CoverageNote coverage={r.coverage} />
            </div>
          )}
        </QueryState>
      </Section>
      <TradeSheet filters={filters} lotId={open} onClose={() => setOpen(null)} />
    </>
  )
}
