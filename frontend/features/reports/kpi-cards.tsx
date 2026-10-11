"use client"

import { QueryState } from "@/components/query-state"
import { useOverview } from "@/features/reports/queries"
import type { ReportFilters } from "@/features/reports/filters"
import {
  count,
  countChange,
  factor,
  money,
  moneyChange,
  percent,
  stamp,
} from "@/features/reports/format"
import { CoverageNote } from "@/features/reports/coverage-note"
import { Kpi, Pnl, Section, Stat, Stats, Unavailable } from "@/features/reports/ui"
import type { Overview } from "@/features/reports/types"

function Change({ text, label = "vs previous period" }: { text: string | null; label?: string }) {
  return text ? (
    <>
      {text} {label}
    </>
  ) : (
    <>No previous period to compare</>
  )
}

function Cards({ o }: { o: Overview }) {
  const decided = o.wins + o.losses + o.breakeven
  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Total signals" sub={<>{count(o.actionableSignals.current)} actionable · <Change text={countChange(o.signals.current, o.signals.previous)} /></>}>
          {count(o.signals.current)}
        </Kpi>
        <Kpi
          label="Accepted signals"
          hint="The decision engine produced an approvable card. This is not your approval and not an order."
          sub={<>{percent(o.acceptanceRate)} acceptance · <Change text={countChange(o.acceptedSignals.current, o.acceptedSignals.previous)} /></>}
        >
          {count(o.acceptedSignals.current)}
        </Kpi>
        <Kpi
          label="Rejected signals"
          hint="The decision engine refused the signal (risk limit, stale, bad contract…). A rejected signal has no P&L."
          sub={<>{percent(o.rejectionRate)} rejection · {o.topRejectionReason ? `top reason: ${o.topRejectionReason}` : "no reason recorded"}</>}
        >
          {count(o.rejectedSignals.current)}
        </Kpi>
        <Kpi
          label="Executed trades"
          hint="Positions that had at least one actual fill in the period. Accepted or approved is not executed."
          sub={<>{o.buyFills} buy / {o.sellFills} sell fills · {o.ordersPending} orders pending · <Change text={countChange(o.executedTrades.current, o.executedTrades.previous)} /></>}
        >
          {count(o.executedTrades.current)}
        </Kpi>
        <Kpi label="Realized P&L" hint="Profit or loss locked in by sells in the period, against each position's average cost. Before fees (not captured)." sub={<Change text={moneyChange(o.realizedPnl.current, o.realizedPnl.previous)} />}>
          <Pnl value={o.realizedPnl.current} />
        </Kpi>
        <Kpi
          label="Unrealized P&L"
          hint="Open positions marked at the current quote. It is today's valuation, not the value at the end of the selected period."
          sub={
            o.unrealizedPnl == null
              ? "No fresh quote for at least one open position"
              : `${o.openPositions} open position${o.openPositions === 1 ? "" : "s"} · valued ${stamp(o.unrealizedAsOf)} (current)`
          }
        >
          <Pnl value={o.unrealizedPnl} />
        </Kpi>
        <Kpi label="Net trading P&L" hint="Realized plus unrealized. Fees and commissions are not captured, so this is before costs." sub="Before fees · not final while positions are open">
          <Pnl value={o.netPnl} />
        </Kpi>
        <Kpi
          label="Win rate"
          hint="Closed trades that made money, out of closed trades with a known result. Open trades and trades with a missing cost basis are left out."
          sub={decided ? `${o.wins} of ${decided} closed trades${o.unknownOutcomes ? ` · ${o.unknownOutcomes} unknown` : ""}` : "No closed trades in this period"}
        >
          {percent(o.winRate)}
        </Kpi>
      </div>
      <Stats>
        <Stat label="Average win">{o.avgWin ? <Pnl value={o.avgWin} /> : <Unavailable reason="no winning trade" />}</Stat>
        <Stat label="Average loss">{o.avgLoss ? <Pnl value={o.avgLoss} /> : <Unavailable reason="no losing trade" />}</Stat>
        <Stat label="Profit factor">{factor(o.profitFactor)}</Stat>
        <Stat label="Largest win">{o.largestWin ? <Pnl value={o.largestWin} /> : <Unavailable reason="no winning trade" />}</Stat>
        <Stat label="Largest loss">{o.largestLoss ? <Pnl value={o.largestLoss} /> : <Unavailable reason="no losing trade" />}</Stat>
        <Stat label="Executed notional">{money(o.executedNotional)}</Stat>
        <Stat label="Approved by you">{count(o.approved)}</Stat>
        <Stat label="Rejected by you">{count(o.rejectedByUser)}</Stat>
        <Stat label="Pending your approval">{count(o.pendingCards)}</Stat>
        <Stat label="Orders submitted">{count(o.ordersSubmitted)}</Stat>
        <Stat label="Orders filled">{count(o.ordersFilled)}</Stat>
        <Stat label="Fees and commissions">
          <Unavailable reason="not captured" />
        </Stat>
      </Stats>
    </>
  )
}

export function OverviewSection({ filters }: { filters: ReportFilters }) {
  const query = useOverview(filters)
  return (
    <Section title="Performance overview" description="Signals, approvals, executions and P&L for the selected period.">
      <QueryState
        query={query}
        loadingLabel="Loading performance"
        emptyTitle="No trading activity yet"
        emptyDescription="Reports fill in from real signals, orders and fills. Nothing here is sample data."
        isEmpty={(o) => o.signals.current === 0 && o.executedTrades.current === 0 && o.openPositions === 0}
      >
        {(o) => (
          <div className="flex flex-col gap-3">
            <Cards o={o} />
            <CoverageNote coverage={o.coverage} />
          </div>
        )}
      </QueryState>
    </Section>
  )
}
