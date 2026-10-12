"use client"

import { QueryState } from "@/components/query-state"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/badge"
import { InstrumentBars } from "@/features/reports/charts"
import { CoverageNote } from "@/features/reports/coverage-note"
import type { ReportFilters } from "@/features/reports/filters"
import { percent, price, stamp, words } from "@/features/reports/format"
import { useInstruments, useSectionParams, type InstrumentParams } from "@/features/reports/queries"
import { ChartFrame, EmptyNote, Notice, Pager, Pnl, Section, SortHead, Unavailable } from "@/features/reports/ui"

const PAGE_SIZE = 25

/** Best and worst instruments by net P&L; an instrument with too few closed trades is marked. */
export function InstrumentPnlChart({ filters }: { filters: ReportFilters }) {
  const query = useInstruments(filters, { page: 1, pageSize: 100, sort: "net_pnl", order: "desc" })
  return (
    <QueryState query={query} loadingLabel="Loading instruments" emptyTitle="No instrument activity in this period" isEmpty={(r) => r.rows.length === 0}>
      {(r) => {
        const rows = r.rows.filter((x) => x.netPnl != null)
        const shown = rows.length > 12 ? [...rows.slice(0, 6), ...rows.slice(-6)] : rows
        const data = shown.map((x) => ({ name: `${x.symbol}${x.lowSample ? " *" : ""}`, value: Number(x.netPnl) }))
        return (
          <ChartFrame
            title="Net P&L by instrument"
            description="* fewer than 5 closed trades: too little to call it a winner or a loser"
            summary={shown.slice(0, 3).map((x) => `${x.symbol} ${x.netPnl}`).join(", ") || "No instruments with a known P&L."}
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead className="text-right">Net P&L</TableHead>
                    <TableHead className="text-right">Closed trades</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((x) => (
                    <TableRow key={`${x.symbol}:${x.instrument}`}>
                      <TableCell>{x.symbol}</TableCell>
                      <TableCell className="text-right">
                        <Pnl value={x.netPnl} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{x.closedTrades}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            }
          >
            {data.length ? <InstrumentBars data={data} /> : <EmptyNote>No instrument has a known P&L yet.</EmptyNote>}
          </ChartFrame>
        )
      }}
    </QueryState>
  )
}

type Props = { filters: ReportFilters; onSelectSymbol: (symbol: string) => void }

export function InstrumentsSection({ filters, onSelectSymbol }: Props) {
  const [params, setParams] = useSectionParams<InstrumentParams>(filters, {
    page: 1,
    sort: "net_pnl",
    order: "desc",
  })
  const query = useInstruments(filters, { ...params, pageSize: PAGE_SIZE })
  const sort = (key: InstrumentParams["sort"]) =>
    setParams({ page: 1, sort: key, order: params.sort === key && params.order === "desc" ? "asc" : "desc" })
  const head = (key: InstrumentParams["sort"], label: string, numeric = true) => (
    <SortHead label={label} active={params.sort === key} order={params.order} onSort={() => sort(key)} numeric={numeric} />
  )
  return (
    <QueryState
      query={query}
      loadingLabel="Loading instrument performance"
      emptyTitle="No instrument activity in this period"
      emptyDescription="Instruments appear once a signal, order or position exists for them."
      isEmpty={(r) => r.totalRows === 0}
    >
      {(r) => (
        <div className="flex flex-col gap-6">
          <Section title="Instrument performance" description="Realized and unrealized are shown apart. Select a symbol to see its trades.">
            <InstrumentPnlChart filters={filters} />
            <Notice>
              Net P&L is realized plus unrealized, before fees. An instrument with fewer than 5 closed trades is marked
              “low sample”: one open position or one lucky trade is not a record.
            </Notice>
            <Table aria-label="Instrument performance">
              <TableHeader>
                <TableRow>
                  {head("symbol", "Symbol", false)}
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Signals (acc / rej)</TableHead>
                  <TableHead className="text-right">Orders (buy / sell)</TableHead>
                  {head("buy_qty", "Qty bought / sold")}
                  <TableHead className="text-right">Open qty</TableHead>
                  <TableHead className="text-right">Avg entry</TableHead>
                  <TableHead className="text-right">Avg exit</TableHead>
                  {head("realized_pnl", "Realized")}
                  <TableHead className="text-right">Unrealized</TableHead>
                  {head("net_pnl", "Net")}
                  {head("win_rate", "Win rate")}
                  {head("closed_trades", "Closed")}
                  <TableHead className="text-right">Fees</TableHead>
                  <TableHead>Last activity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.rows.map((x) => (
                  <TableRow key={`${x.symbol}:${x.instrument}`}>
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => onSelectSymbol(x.symbol)}
                        className="font-mono font-medium underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`Show trades for ${x.symbol}`}
                      >
                        {x.symbol}
                      </button>
                    </TableCell>
                    <TableCell>{words(x.instrument)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {x.signals} ({x.acceptedSignals} / {x.rejectedSignals})
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {x.buyOrders} / {x.sellOrders}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {x.buyQty} / {x.sellQty}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{x.openQty}</TableCell>
                    <TableCell className="text-right tabular-nums">{price(x.avgEntryPrice)}</TableCell>
                    <TableCell className="text-right tabular-nums">{price(x.avgExitPrice)}</TableCell>
                    <TableCell className="text-right">
                      <Pnl value={x.realizedPnl} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Pnl value={x.unrealizedPnl} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Pnl value={x.netPnl} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {percent(x.winRate)}
                      {x.lowSample ? <Badge className="ml-1 text-[10px]">low sample</Badge> : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{x.closedTrades}</TableCell>
                    <TableCell className="text-right">
                      <Unavailable reason="not captured" />
                    </TableCell>
                    <TableCell>{stamp(x.lastActivity)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager page={params.page} pageSize={PAGE_SIZE} total={r.totalRows} onPage={(page) => setParams({ ...params, page })} />
          </Section>
          <CoverageNote coverage={r.coverage} />
        </div>
      )}
    </QueryState>
  )
}
