"use client"

import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ExecutionSection } from "@/features/reports/execution-section"
import { FilterBar } from "@/features/reports/filter-bar"
import { DEFAULT_FILTERS, type ReportFilters } from "@/features/reports/filters"
import { InstrumentsSection } from "@/features/reports/instruments-section"
import { OverviewSection } from "@/features/reports/kpi-cards"
import { PnlSection } from "@/features/reports/pnl-section"
import { SignalsSection } from "@/features/reports/signals-section"
import { TradesSection } from "@/features/reports/trades-section"

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "signals", label: "Signals" },
  { value: "execution", label: "Execution" },
  { value: "pnl", label: "P&L" },
  { value: "instruments", label: "Instruments" },
  { value: "trades", label: "Trades" },
] as const
type Tab = (typeof TABS)[number]["value"]

export function ReportsScreen() {
  const [filters, setFilters] = useState<ReportFilters>(DEFAULT_FILTERS)
  const [tab, setTab] = useState<Tab>("overview")
  return (
    <TooltipProvider>
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          Analyze signal quality, execution activity, realized returns, and trading performance.
        </p>
        <FilterBar filters={filters} onChange={setFilters} />
        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="gap-4">
          <div className="overflow-x-auto">
            <TabsList variant="line" aria-label="Report sections">
              {TABS.map((t) => (
                <TabsTrigger key={t.value} value={t.value}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value="overview">
            <OverviewSection filters={filters} />
          </TabsContent>
          <TabsContent value="signals">
            <SignalsSection filters={filters} />
          </TabsContent>
          <TabsContent value="execution">
            <ExecutionSection filters={filters} />
          </TabsContent>
          <TabsContent value="pnl">
            <PnlSection filters={filters} />
          </TabsContent>
          <TabsContent value="instruments">
            <InstrumentsSection
              filters={filters}
              onSelectSymbol={(symbol) => {
                setFilters({ ...filters, symbol })
                setTab("trades")
              }}
            />
          </TabsContent>
          <TabsContent value="trades">
            <TradesSection filters={filters} />
          </TabsContent>
        </Tabs>
      </div>
    </TooltipProvider>
  )
}
