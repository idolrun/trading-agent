"use client"

import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/query-state"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { apiGet } from "@/lib/api/client"
import { CumulativeLine, GroupedBars, PnlBars, SERIES } from "@/features/reports/charts"
import { CoverageNote } from "@/features/reports/coverage-note"
import { isReady, toParams, type ReportFilters } from "@/features/reports/filters"
import { count, duration, factor, percent } from "@/features/reports/format"
import { InstrumentPnlChart } from "@/features/reports/instruments-section"
import { usePnl } from "@/features/reports/queries"
import type { PnlReport, TimeseriesReport } from "@/features/reports/types"
import { ChartFrame, Notice, Pnl, Section, Stat, Stats, Unavailable } from "@/features/reports/ui"

function WinLoss({ r }: { r: PnlReport }) {
  const decided = r.wins + r.losses + r.breakeven
  const parts = [
    { key: "wins", label: "Winning", n: r.wins, cls: "bg-success" },
    { key: "losses", label: "Losing", n: r.losses, cls: "bg-destructive" },
    { key: "breakeven", label: "Break-even", n: r.breakeven, cls: "bg-muted-foreground" },
  ]
  return (
    <ChartFrame
      title="Winning vs losing closed trades"
      description="Trades fully closed in the period"
      summary={`${r.wins} winning, ${r.losses} losing, ${r.breakeven} break-even closed trades; win rate ${percent(r.winRate)}.`}
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Outcome</TableHead>
              <TableHead className="text-right">Trades</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {parts.map((p) => (
              <TableRow key={p.key}>
                <TableCell>{p.label}</TableCell>
                <TableCell className="text-right tabular-nums">{p.n}</TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell>Unknown result (no cost basis)</TableCell>
              <TableCell className="text-right tabular-nums">{r.unknownOutcomes}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex h-4 overflow-hidden rounded-sm bg-muted" aria-hidden>
          {decided
            ? parts.map((p) => <span key={p.key} className={p.cls} style={{ width: `${(p.n / decided) * 100}%` }} />)
            : null}
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {parts.map((p) => (
            <li key={p.key} className="flex items-center gap-1.5">
              <span aria-hidden className={`size-2.5 rounded-sm ${p.cls}`} />
              {p.label}: <span className="font-mono tabular-nums">{p.n}</span>
            </li>
          ))}
          <li className="text-muted-foreground">Win rate {percent(r.winRate)}</li>
        </ul>
      </div>
    </ChartFrame>
  )
}

function WinLossOverTime({ filters }: { filters: ReportFilters }) {
  const params = { ...toParams(filters), metrics: "wins,losses" }
  const query = useQuery({
    queryKey: ["reports", "winloss-trend", params],
    queryFn: () => apiGet<TimeseriesReport>("/api/v1/reports/trading/timeseries", params),
    staleTime: 30_000,
    enabled: isReady(filters),
  })
  return (
    <QueryState query={query} loadingLabel="Loading trend" emptyTitle="No closed trades in this period" isEmpty={(t) => t.points.every((p) => !p.hasActivity)}>
      {(t) => {
        const data = t.points.map((p) => ({ bucket: p.bucket, wins: Number(p.values.wins ?? 0), losses: Number(p.values.losses ?? 0) }))
        return (
          <ChartFrame
            title="Winning vs losing trades over time"
            description={`Trades closed per ${t.granularity}`}
            summary={`${data.reduce((n, p) => n + p.wins, 0)} winning and ${data.reduce((n, p) => n + p.losses, 0)} losing trades.`}
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Bucket</TableHead>
                    <TableHead className="text-right">Winning</TableHead>
                    <TableHead className="text-right">Losing</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((p) => (
                    <TableRow key={p.bucket}>
                      <TableCell>{p.bucket}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.wins}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.losses}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            }
          >
            <GroupedBars
              data={data}
              series={[
                { key: "wins", label: "Winning", color: SERIES.gain },
                { key: "losses", label: "Losing", color: SERIES.loss },
              ]}
            />
          </ChartFrame>
        )
      }}
    </QueryState>
  )
}

function Body({ r, filters }: { r: PnlReport; filters: ReportFilters }) {
  const cumulative = r.series.map((p) => ({ name: p.bucket, value: p.cumulative == null ? null : Number(p.cumulative) }))
  const perBucket = r.series.map((p) => ({
    name: p.bucket,
    value: p.hasActivity && p.realized != null ? Number(p.realized) : null,
  }))
  const table = (rows: { name: string; value: number | null }[], header: string) => (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Bucket</TableHead>
          <TableHead className="text-right">{header}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((p) => (
          <TableRow key={p.name}>
            <TableCell>{p.name}</TableCell>
            <TableCell className="text-right">{p.value == null ? <Unavailable /> : <Pnl value={String(p.value)} />}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
  return (
    <div className="flex flex-col gap-6">
      <Section title="Profit and loss" description="Realized comes from sells in the period; unrealized is the open positions marked now.">
        <Stats>
          <Stat label="Realized">
            <Pnl value={r.realized} />
          </Stat>
          <Stat label="Unrealized (now)">
            <Pnl value={r.unrealized} />
          </Stat>
          <Stat label="Net (before costs)">
            <Pnl value={r.net} />
          </Stat>
          <Stat label="Gross profit">
            <Pnl value={r.grossProfit} />
          </Stat>
          <Stat label="Gross loss">
            <Pnl value={r.grossLoss} />
          </Stat>
          <Stat label="Fees and commissions">
            <Unavailable reason="not captured" />
          </Stat>
          <Stat label="Net after costs">
            <Unavailable reason="costs are not captured" />
          </Stat>
          <Stat label="Win rate">{percent(r.winRate)}</Stat>
          <Stat label="Average win">
            <Pnl value={r.avgWin} />
          </Stat>
          <Stat label="Average loss">
            <Pnl value={r.avgLoss} />
          </Stat>
          <Stat label="Largest win">
            <Pnl value={r.largestWin} />
          </Stat>
          <Stat label="Largest loss">
            <Pnl value={r.largestLoss} />
          </Stat>
          <Stat label="Profit factor">{factor(r.profitFactor)}</Stat>
          <Stat label="Average holding time">{duration(r.avgHoldSeconds)}</Stat>
          <Stat label="Closed, result unknown">{count(r.unknownOutcomes)}</Stat>
        </Stats>
        {r.unknownOutcomes ? (
          <Notice tone="warning">
            {r.unknownOutcomes} closed trade{r.unknownOutcomes === 1 ? " has" : "s have"} no recorded cost basis, so
            realized P&L and win rate leave {r.unknownOutcomes === 1 ? "it" : "them"} out rather than guess.
          </Notice>
        ) : null}
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="Cumulative realized P&L"
          description="Running total of realized P&L within the period; the dashed line is break-even"
          summary={`Cumulative realized P&L ends at ${r.realized ?? "unavailable"} after ${r.series.length} ${r.coverage.window.granularity} buckets.`}
          table={table(cumulative, "Cumulative")}
        >
          <CumulativeLine data={cumulative} label="Cumulative realized P&L" />
        </ChartFrame>
        <ChartFrame
          title="Realized P&L per period bucket"
          description="Gains solid, losses hatched. Buckets without a sell are left empty"
          summary={`Realized P&L per ${r.coverage.window.granularity}; ${r.series.filter((p) => p.hasActivity).length} buckets had activity.`}
          table={table(perBucket, "Realized")}
        >
          <PnlBars data={perBucket} label="Realized P&L" />
        </ChartFrame>
        <WinLoss r={r} />
        <WinLossOverTime filters={filters} />
      </div>
      <Section title="Profit by instrument" description="Net P&L (realized + unrealized) per instrument">
        <InstrumentPnlChart filters={filters} />
      </Section>
      <CoverageNote coverage={r.coverage} />
    </div>
  )
}

export function PnlSection({ filters }: { filters: ReportFilters }) {
  const query = usePnl(filters)
  return (
    <QueryState
      query={query}
      loadingLabel="Loading profit and loss"
      emptyTitle="No profit or loss yet"
      emptyDescription="P&L comes from real fills: a sell against a position with a cost basis."
      isEmpty={(r) => r.series.every((p) => !p.hasActivity) && r.wins + r.losses + r.breakeven + r.unknownOutcomes === 0 && r.unrealized === "0"}
    >
      {(r) => <Body r={r} filters={filters} />}
    </QueryState>
  )
}
