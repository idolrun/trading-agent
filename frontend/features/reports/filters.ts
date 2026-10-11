export type Period = "1d" | "3d" | "7d" | "30d" | "all" | "custom"
export type ModeChoice = "default" | "shadow" | "paper" | "live" | "all"

export const PERIODS: readonly { value: Period; label: string; hint: string }[] = [
  { value: "1d", label: "1D", hint: "Today so far (ET)" },
  { value: "3d", label: "3D", hint: "Last 3 ET calendar days, including today" },
  { value: "7d", label: "1W", hint: "Last 7 ET calendar days, including today" },
  { value: "30d", label: "1M", hint: "Last 30 ET calendar days, including today" },
  { value: "all", label: "Overall", hint: "All recorded history" },
  { value: "custom", label: "Custom", hint: "Pick a start and end date (ET, end date included)" },
]

export const MODES: readonly { value: ModeChoice; label: string }[] = [
  { value: "default", label: "Current trading mode" },
  { value: "shadow", label: "Shadow (simulated)" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
  { value: "all", label: "All modes combined" },
]

export type ReportFilters = {
  period: Period
  startDate: string
  endDate: string
  mode: ModeChoice
  symbol: string
  instrument: "" | "option" | "stock"
}

export const DEFAULT_FILTERS: ReportFilters = {
  period: "7d",
  startDate: "",
  endDate: "",
  mode: "default",
  symbol: "",
  instrument: "",
}

const SYMBOL = /^[A-Za-z][A-Za-z.-]{0,9}$/

/** A custom period needs both dates, in order, and a symbol is only sent when it is valid. */
export function isReady(f: ReportFilters): boolean {
  if (f.symbol && !SYMBOL.test(f.symbol)) return false
  if (f.period !== "custom") return true
  return Boolean(f.startDate && f.endDate && f.startDate <= f.endDate)
}

/** Query parameters shared by every report endpoint (the backend's own names). */
export function toParams(f: ReportFilters) {
  return {
    period: f.period,
    startDate: f.period === "custom" ? f.startDate : undefined,
    endDate: f.period === "custom" ? f.endDate : undefined,
    mode: f.mode === "default" ? undefined : f.mode,
    symbol: f.symbol || undefined,
    instrument: f.instrument || undefined,
  }
}
