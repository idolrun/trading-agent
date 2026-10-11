"use client"

import { useState } from "react"
import { useIsFetching, useQueryClient } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { MODES, PERIODS, isReady, type ModeChoice, type Period, type ReportFilters } from "@/features/reports/filters"
import { reportKeys } from "@/features/reports/queries"

export function FilterBar({
  filters,
  onChange,
}: {
  filters: ReportFilters
  onChange: (next: ReportFilters) => void
}) {
  const client = useQueryClient()
  const fetching = useIsFetching({ queryKey: reportKeys.all }) > 0
  const set = (patch: Partial<ReportFilters>) => onChange({ ...filters, ...patch })
  const hint = PERIODS.find((p) => p.value === filters.period)?.hint
  return (
    <form
      aria-label="Report filters"
      className="flex flex-col gap-3 rounded-md border bg-card p-3"
      onSubmit={(e) => e.preventDefault()}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          spacing={0}
          value={filters.period}
          onValueChange={(v) => v && set({ period: v as Period })}
          aria-label="Reporting period"
        >
          {PERIODS.map((p) => (
            <ToggleGroupItem key={p.value} value={p.value} aria-label={`${p.label}: ${p.hint}`} title={p.hint}>
              {p.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void client.invalidateQueries({ queryKey: reportKeys.all })}
          disabled={fetching}
        >
          <RefreshCw aria-hidden className={fetching ? "animate-spin motion-reduce:animate-none" : undefined} />
          {fetching ? "Refreshing" : "Refresh"}
        </Button>
      </div>
      {filters.period === "custom" ? (
        <div className="flex flex-wrap items-end gap-3 text-xs">
          <label className="flex flex-col gap-1">
            Start date (ET)
            <Input type="date" value={filters.startDate} max={filters.endDate || undefined} onChange={(e) => set({ startDate: e.target.value })} className="h-8" />
          </label>
          <label className="flex flex-col gap-1">
            End date (ET, included)
            <Input type="date" value={filters.endDate} min={filters.startDate || undefined} onChange={(e) => set({ endDate: e.target.value })} className="h-8" />
          </label>
          {!isReady(filters) ? <span role="status" className="text-muted-foreground">Choose a start date, then an end date on or after it.</span> : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-end gap-3 text-xs">
        <label className="flex flex-col gap-1">
          Mode
          <NativeSelect size="sm" value={filters.mode} onChange={(e) => set({ mode: e.target.value as ModeChoice })}>
            {MODES.map((m) => (
              <NativeSelectOption key={m.value} value={m.value}>
                {m.label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        <label className="flex flex-col gap-1">
          Instrument type
          <NativeSelect size="sm" value={filters.instrument} onChange={(e) => set({ instrument: e.target.value as ReportFilters["instrument"] })}>
            <NativeSelectOption value="">Stocks and options</NativeSelectOption>
            <NativeSelectOption value="stock">Stocks</NativeSelectOption>
            <NativeSelectOption value="option">Options</NativeSelectOption>
          </NativeSelect>
        </label>
        <SymbolField key={filters.symbol} applied={filters.symbol} onCommit={(symbol) => set({ symbol })} />
      </div>
      <p className="text-xs text-muted-foreground">{hint}. Day boundaries are America/New_York; unrealized P&L is always the current valuation.</p>
    </form>
  )
}

/** Keyed by the applied symbol, so a symbol chosen elsewhere (an instrument row) resets the text. */
function SymbolField({ applied, onCommit }: { applied: string; onCommit: (symbol: string) => void }) {
  const [text, setText] = useState(applied)
  const invalid = text !== "" && !/^[A-Za-z][A-Za-z.-]{0,9}$/.test(text)
  const commit = () => !invalid && text.toUpperCase() !== applied && onCommit(text.toUpperCase())
  return (
    <>
      <label className="flex flex-col gap-1">
        Symbol
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), commit())}
          placeholder="All symbols"
          aria-invalid={invalid}
          className="h-8 w-32 uppercase"
        />
      </label>
      {applied ? (
        <Button type="button" size="sm" variant="ghost" onClick={() => onCommit("")}>
          Clear symbol
        </Button>
      ) : null}
      {invalid ? (
        <span role="alert" className="text-destructive">
          Use letters only, for example AAPL.
        </span>
      ) : null}
    </>
  )
}
