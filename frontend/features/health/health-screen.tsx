"use client"

import { useId, useState, type ReactNode } from "react"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { useHealthDetails } from "@/features/health/queries"
import { HEALTH_WINDOWS, type HealthDetails, type HealthWindow } from "@/features/health/types"
import { formatTime } from "@/lib/format"

const FIELD =
  "h-9 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

const ms = (value: number) => `${value.toLocaleString("en-US", { maximumFractionDigits: 1 })} ms`
const seconds = (value: number | null) =>
  value == null ? "—" : `${value.toLocaleString("en-US", { maximumFractionDigits: 1 })} s`
const percent = (ratio: number) => `${(ratio * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`
const usd = (value: string) =>
  `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`
const yesNo = (value: boolean | null) => (value == null ? "unknown" : value ? "yes" : "no")

type Cell = ReactNode

/** A titled table; `numeric` columns are right-aligned. Empty `rows` show a note instead. */
function Table({
  title,
  head,
  rows,
  numeric = [],
}: {
  title: string
  head: string[]
  rows: Cell[][]
  numeric?: number[]
}) {
  const id = useId()
  return (
    <section className="flex flex-col gap-2">
      <h2 id={id} className="text-lg font-medium">
        {title}
      </h2>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No data in this window.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table aria-labelledby={id} className="w-full text-sm">
            <thead className="bg-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                {head.map((label, column) => (
                  <th key={label} scope="col" className={`px-3 py-2 font-medium ${numeric.includes(column) ? "text-right" : ""}`}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, column) => (
                    <td key={column} className={`px-3 py-2 tabular-nums ${numeric.includes(column) ? "text-right" : ""}`}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

const metrics = (title: string, rows: [string, Cell][]) => (
  <Table key={title} title={title} head={["Metric", "Value"]} rows={rows} numeric={[1]} />
)

function Details({ health }: { health: HealthDetails }) {
  const { llm, messages, orders, reconciliation } = health
  const budget = llm.monthlyBudgetUsd
  const spendVsBudget =
    budget == null
      ? `${usd(llm.spendMonthToDateUsd)} (no monthly budget set)`
      : `${usd(llm.spendMonthToDateUsd)} of ${usd(budget)}${
          Number(budget) > 0 ? ` (${percent(Number(llm.spendMonthToDateUsd) / Number(budget))})` : ""
        }`

  return (
    <div className="flex flex-col gap-6">
      {metrics("Backlog", [
        ["Status", words(health.status)],
        ["Backlog depth", `${health.backlogDepth} messages`],
        ["Oldest unprocessed", seconds(health.oldestUnprocessedSeconds)],
        ["Catch-up", health.catchUp ? "in progress" : "no"],
        ["Feed silence", seconds(health.feedSilenceSeconds)],
        ["Feed heartbeat age", seconds(health.feedHeartbeatAgeSeconds)],
        ["Heartbeat age", seconds(health.heartbeatAgeSeconds)],
      ])}
      <Table
        title="Latency by stage"
        head={["Stage", "Path", "Count", "p50", "p95", "p99"]}
        numeric={[2, 3, 4, 5]}
        rows={health.latency.map((row) => [row.stage, row.path, row.count, ms(row.p50Ms), ms(row.p95Ms), ms(row.p99Ms)])}
      />
      <Table
        title="Queue wait per provider"
        head={["Provider", "Count", "p95", "Max"]}
        numeric={[1, 2, 3]}
        rows={health.queueWait.map((row) => [row.providerId, row.count, ms(row.p95Ms), ms(row.maxMs)])}
      />
      {metrics("Messages", [
        ["Total", messages.total],
        ["Late", messages.late],
        ["Stale", messages.stale],
        ["Backfilled", messages.backfilled],
        ["Failed", messages.failed],
        ["Duplicates since start", messages.duplicatesSinceStart],
      ])}
      {metrics("LLM spend", [
        ["Month to date vs budget", spendVsBudget],
        ["Slow-path share", llm.slowPathShare == null ? "—" : percent(llm.slowPathShare)],
      ])}
      <Table
        title="LLM models"
        head={["Provider", "Model", "Calls", "p50", "p95", "Error rate", "Fallback calls", "Spend"]}
        numeric={[2, 3, 4, 5, 6, 7]}
        rows={llm.models.map((row) => [
          row.provider,
          row.model,
          row.calls,
          ms(row.latencyP50Ms),
          ms(row.latencyP95Ms),
          percent(row.errorRate),
          row.fallbackCalls,
          usd(row.spendUsd),
        ])}
      />
      <Table
        title="LLM providers"
        head={["Provider", "Breaker", "Slots", "Semaphore waits", "Rate limited (429)"]}
        numeric={[2, 3, 4]}
        rows={llm.providers.map((row) => [
          row.provider,
          row.breakerOpen == null ? "unused" : row.breakerOpen ? "open" : "closed",
          row.slotsSinceStart,
          row.semaphoreWaitsSinceStart,
          row.rateLimitedSinceStart,
        ])}
      />
      <Table
        title="Card outcomes"
        head={["To state", "Reason", "Count"]}
        numeric={[2]}
        rows={health.cardOutcomes.map((row) => [words(row.toState), words(row.reasonCode), row.count])}
      />
      <Table
        title="Orders by status"
        head={["Status", "Count"]}
        numeric={[1]}
        rows={Object.entries(orders.byStatus).map(([status, count]) => [words(status), count])}
      />
      {metrics("Order events", [
        ["Rejected", orders.rejected],
        ["Re-prices", orders.reprices],
      ])}
      {metrics("Reconciliation", [
        ["Last run", reconciliation.lastRunAt ? formatTime(reconciliation.lastRunAt) : "none since start"],
        ["Clean", yesNo(reconciliation.clean)],
        ["Mismatches", reconciliation.mismatches],
        ["Unknown orders", reconciliation.unknownOrders],
        ["Mismatch alerts", reconciliation.mismatchAlerts],
        ["Error", reconciliation.error ?? "none"],
      ])}
      {metrics("Invariants and database", [
        ["Invariant blocks", health.invariantBlocks],
        ["Postgres connections", health.postgres.connections],
        ["Postgres lock waits", health.postgres.lockWaits],
      ])}
    </div>
  )
}

export function HealthScreen() {
  const [range, setRange] = useState<HealthWindow>("session")
  const query = useHealthDetails(range)

  return (
    <div className="flex flex-col gap-4">
      <label className="flex w-fit flex-col gap-1 text-sm">
        Window
        <select className={FIELD} value={range} onChange={(event) => setRange(event.target.value as HealthWindow)}>
          {HEALTH_WINDOWS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <QueryState query={query} loadingLabel="Loading system health" emptyTitle="No health data">
        {(health) => <Details health={health} />}
      </QueryState>
    </div>
  )
}
