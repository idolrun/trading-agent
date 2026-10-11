"use client"

import { useState } from "react"
import { QueryState } from "@/components/query-state"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import { CountBars, GroupedBars, SERIES } from "@/features/reports/charts"
import { CoverageNote } from "@/features/reports/coverage-note"
import { isReady, toParams, type ReportFilters } from "@/features/reports/filters"
import { count, percent, providerName, stamp, words } from "@/features/reports/format"
import { useSignals, type SignalParams } from "@/features/reports/queries"
import type { SignalsReport, TimeseriesReport } from "@/features/reports/types"
import { ChartFrame, EmptyNote, Notice, Pager, Pnl, Section, Stat, Stats, StatusBadge } from "@/features/reports/ui"

const PAGE_SIZE = 25

function Funnel({ r }: { r: SignalsReport }) {
  const top = Math.max(1, r.funnel[0]?.count ?? 1)
  return (
    <ol className="flex flex-col gap-2" aria-label="Signal funnel, one stage after another">
      {r.funnel.map((s, i) => (
        <li key={s.key} className="grid grid-cols-[minmax(0,14rem)_1fr_auto] items-center gap-3 text-sm">
          <span>
            <span className="text-muted-foreground tabular-nums">{i + 1}. </span>
            {s.label}
          </span>
          <span className="h-2 overflow-hidden rounded-sm bg-muted" aria-hidden>
            <span className="block h-full bg-info" style={{ width: `${Math.max(2, (s.count / top) * 100)}%` }} />
          </span>
          <span className="font-mono text-xs tabular-nums">
            {count(s.count)}
            {s.rateOfPrevious != null ? <span className="text-muted-foreground"> · {percent(s.rateOfPrevious, 0)} of previous</span> : null}
          </span>
        </li>
      ))}
    </ol>
  )
}

function useTrend(filters: ReportFilters) {
  const params = { ...toParams(filters), metrics: "accepted_signals,rejected_signals" }
  return useQuery({
    queryKey: ["reports", "signal-trend", params],
    queryFn: () => apiGet<TimeseriesReport>("/api/v1/reports/trading/timeseries", params),
    staleTime: 30_000,
    enabled: isReady(filters),
  })
}

function Trend({ filters }: { filters: ReportFilters }) {
  const query = useTrend(filters)
  return (
    <QueryState query={query} loadingLabel="Loading trend" emptyTitle="No signals in this period" isEmpty={(t) => t.points.every((p) => !p.hasActivity)}>
      {(t) => {
        const data = t.points.map((p) => ({
          bucket: p.bucket,
          accepted: Number(p.values.accepted_signals ?? 0),
          rejected: Number(p.values.rejected_signals ?? 0),
        }))
        const a = data.reduce((n, p) => n + p.accepted, 0)
        const r = data.reduce((n, p) => n + p.rejected, 0)
        return (
          <ChartFrame
            title="Accepted vs rejected over time"
            description={`One bar group per ${t.granularity}`}
            summary={`${a} accepted and ${r} rejected signals across ${data.length} ${t.granularity} buckets.`}
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Bucket</TableHead>
                    <TableHead className="text-right">Accepted</TableHead>
                    <TableHead className="text-right">Rejected</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((p) => (
                    <TableRow key={p.bucket}>
                      <TableCell>{p.bucket}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.accepted}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.rejected}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            }
          >
            <GroupedBars
              data={data}
              series={[
                { key: "accepted", label: "Accepted", color: SERIES.gain },
                { key: "rejected", label: "Rejected", color: SERIES.loss },
              ]}
            />
          </ChartFrame>
        )
      }}
    </QueryState>
  )
}

function Outcomes({ r }: { r: SignalsReport }) {
  const o = r.outcomes
  return (
    <Section
      title="Did accepted signals make money?"
      description="Accepted entry and add signals followed through to their actual fills and closed positions."
    >
      <Stats>
        <Stat label="Accepted signals">{count(o.acceptedSignals)}</Stat>
        <Stat label="Reached an order">{count(o.withOrders)}</Stat>
        <Stat label="Actually filled">{count(o.withFills)}</Stat>
        <Stat label="Closed, attributed">{count(o.closedAttributed)}</Stat>
        <Stat label="Profitable">{count(o.profitable)}</Stat>
        <Stat label="Losing">{count(o.losing)}</Stat>
        <Stat label="Break-even">{count(o.breakeven)}</Stat>
        <Stat label="Still open">{count(o.stillOpen)}</Stat>
        <Stat label="Attribution unavailable">{count(o.attributionUnavailable)}</Stat>
        <Stat label="Realized P&L (attributed)">
          <Pnl value={o.realizedPnl} />
        </Stat>
        <Stat label="Average per outcome">
          <Pnl value={o.avgRealizedPerOutcome} />
        </Stat>
        <Stat label="Win rate">{percent(o.winRate)}</Stat>
      </Stats>
      <Notice>
        A signal gets a result only when it is the single source of its position. Positions fed by several signals, by a
        manual entry, or with no cost basis are counted as “attribution unavailable” instead of being split. A profitable
        position does not prove the signal caused the profit. Rejected signals never carry P&L.
      </Notice>
    </Section>
  )
}

export function SignalsSection({ filters }: { filters: ReportFilters }) {
  const [params, setParams] = useState<SignalParams>({ page: 1, engine: "" })
  const query = useSignals(filters, params)
  return (
    <QueryState
      query={query}
      loadingLabel="Loading signal analytics"
      emptyTitle="No signals in this period"
      emptyDescription="Signals appear when messages are received from the feed."
      isEmpty={(r) => r.funnel[0]?.count === 0}
    >
      {(r) => (
        <div className="flex flex-col gap-6">
          <Section title="Signal funnel" description="Each stage is counted from its own record: accepted ≠ approved ≠ ordered ≠ filled.">
            <Funnel r={r} />
            <Stats>
              <Stat label="Approval of accepted">{percent(r.conversionToApproval)}</Stat>
              <Stat label="Execution of accepted">{percent(r.conversionToExecution)}</Stat>
            </Stats>
          </Section>
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartFrame
              title="Engine decision"
              description="Every signal received, by what the decision engine did"
              summary={r.engineStatus.map((s) => `${s.label}: ${s.count}`).join(", ")}
              table={<LabelTable rows={r.engineStatus} />}
            >
              <CountBars data={r.engineStatus.map((s) => ({ name: s.label, value: s.count }))} label="Signals" />
            </ChartFrame>
            <ChartFrame
              title="Your decision on accepted signals"
              summary={r.approvalStatus.map((s) => `${s.label}: ${s.count}`).join(", ")}
              table={<LabelTable rows={r.approvalStatus} />}
            >
              <CountBars data={r.approvalStatus.map((s) => ({ name: s.label, value: s.count }))} label="Signals" color="var(--warning)" />
            </ChartFrame>
            <ChartFrame
              title="Why signals did not go through"
              description="Engine rejections, your rejections, expiries and broker refusals"
              summary={r.reasons.length ? r.reasons.slice(0, 5).map((s) => `${s.reason}: ${s.count}`).join(", ") : "No rejections in this period."}
              table={
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Source</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.reasons.map((x) => (
                      <TableRow key={`${x.source}:${x.reason}`}>
                        <TableCell>{words(x.source)}</TableCell>
                        <TableCell>{x.reason}</TableCell>
                        <TableCell className="text-right tabular-nums">{x.count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              }
            >
              {r.reasons.length ? (
                <CountBars data={r.reasons.slice(0, 8).map((x) => ({ name: `${words(x.source)}: ${x.reason}`.slice(0, 40), value: x.count }))} label="Signals" color={SERIES.loss} />
              ) : (
                <EmptyNote>No rejections or lapses in this period.</EmptyNote>
              )}
            </ChartFrame>
            <Trend filters={filters} />
          </div>
          <Section title="By symbol and by provider" description="Actionable signals only">
            <div className="grid gap-4 lg:grid-cols-2">
              <Table aria-label="Signals by symbol">
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead className="text-right">Signals</TableHead>
                    <TableHead className="text-right">Accepted</TableHead>
                    <TableHead className="text-right">Rejected</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.bySymbol.map((s) => (
                    <TableRow key={s.symbol ?? "unknown"}>
                      <TableCell>{s.symbol ?? "unknown"}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.total}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.accepted}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.rejected}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Table aria-label="Signals by provider">
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead className="text-right">Signals</TableHead>
                    <TableHead className="text-right">Accepted</TableHead>
                    <TableHead className="text-right">Rejected</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.byProvider.map((p) => (
                    <TableRow key={p.providerId}>
                      <TableCell>{providerName(p.providerId, p.name)}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.total}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.accepted}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.rejected}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Section>
          <Outcomes r={r} />
          <Section
            title="Signal log"
            actions={
              <label className="flex items-center gap-2 text-xs">
                Engine decision
                <NativeSelect
                  size="sm"
                  value={params.engine}
                  onChange={(e) => setParams({ page: 1, engine: e.target.value as SignalParams["engine"] })}
                >
                  <NativeSelectOption value="">All signals</NativeSelectOption>
                  <NativeSelectOption value="accepted">Accepted</NativeSelectOption>
                  <NativeSelectOption value="rejected">Rejected</NativeSelectOption>
                  <NativeSelectOption value="no_card">No card</NativeSelectOption>
                </NativeSelect>
              </label>
            }
          >
            <Table aria-label="Signal log">
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Engine</TableHead>
                  <TableHead>Your decision</TableHead>
                  <TableHead>Execution</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.rows.map((x) => (
                  <TableRow key={x.signalId}>
                    <TableCell>{stamp(x.at)}</TableCell>
                    <TableCell>{x.symbol ?? "—"}</TableCell>
                    <TableCell>{words(x.msgType)}</TableCell>
                    <TableCell>
                      <StatusBadge status={x.engineStatus} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={x.approvalStatus} />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={x.executionStatus} />
                    </TableCell>
                    <TableCell className="max-w-64 truncate" title={x.reason ?? undefined}>
                      {x.reason ?? "—"}
                    </TableCell>
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

function LabelTable({ rows }: { rows: { key: string; label: string; count: number }[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Signals</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((x) => (
          <TableRow key={x.key}>
            <TableCell>{x.label}</TableCell>
            <TableCell className="text-right tabular-nums">{x.count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
