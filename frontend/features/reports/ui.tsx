"use client"

import type { ReactNode } from "react"
import { ArrowDown, ArrowDownRight, ArrowUp, ArrowUpDown, ArrowUpRight, CircleHelp, Minus } from "lucide-react"
import { cn } from "cn"
import { Badge } from "@/components/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { TableHead } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { direction, signedMoney, UNAVAILABLE, words } from "@/features/reports/format"

/** Gain and loss use text + glyph + sign. Colour only reinforces. */
const DIRECTION_CLASS = {
  gain: "text-success-foreground",
  loss: "text-destructive",
  flat: "text-muted-foreground",
  unavailable: "text-muted-foreground",
} as const

const TONE_CLASS = {
  success: "border-success/40 bg-success-muted text-success-foreground",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  warning: "border-warning/40 bg-warning-muted text-warning-foreground",
  info: "border-info/40 bg-info-muted text-info-foreground",
  neutral: "text-muted-foreground",
} as const
type Tone = keyof typeof TONE_CLASS

/** A value the backend could not compute. Distinct from a real zero. */
export function Unavailable({ reason }: { reason?: string }) {
  return (
    <span className="text-muted-foreground" title={reason}>
      {UNAVAILABLE}
      <span className="sr-only">not available{reason ? `: ${reason}` : ""}</span>
    </span>
  )
}

export function Pnl({ value, className }: { value: string | null | undefined; className?: string }) {
  const d = direction(value)
  if (d === "unavailable") return <Unavailable />
  const Icon = d === "gain" ? ArrowUpRight : d === "loss" ? ArrowDownRight : Minus
  const label = d === "gain" ? "profit" : d === "loss" ? "loss" : "break-even"
  return (
    <span className={cn("inline-flex items-center gap-1 font-mono tabular-nums", DIRECTION_CLASS[d], className)}>
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {signedMoney(value)}
      <span className="sr-only"> ({label})</span>
    </span>
  )
}

export function Hint({ children }: { children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label="More information" className="text-muted-foreground hover:text-foreground">
          <CircleHelp aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{children}</TooltipContent>
    </Tooltip>
  )
}

export function Kpi({
  label,
  hint,
  children,
  sub,
}: {
  label: string
  hint?: ReactNode
  children: ReactNode
  sub?: ReactNode
}) {
  return (
    <Card size="sm" className="gap-1">
      <CardHeader className="pb-0">
        <CardDescription className="flex items-center gap-1.5 text-xs font-semibold tracking-wide uppercase">
          {label}
          {hint ? <Hint>{hint}</Hint> : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-0.5">
        <div className="font-mono text-2xl font-semibold tabular-nums">{children}</div>
        {sub ? <div className="text-xs text-muted-foreground">{sub}</div> : null}
      </CardContent>
    </Card>
  )
}

export function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border px-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm font-medium tabular-nums">{children}</dd>
    </div>
  )
}

export function Stats({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">{children}</dl>
}

export function Section({
  title,
  description,
  children,
  actions,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold tracking-wide uppercase">{title}</h2>
          {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}

/**
 * A chart with a text summary for screen readers and its numbers in a table behind
 * "Data table": a chart alone is not an accessible representation of the data.
 */
export function ChartFrame({
  title,
  description,
  summary,
  table,
  children,
}: {
  title: string
  description?: string
  summary: string
  table: ReactNode
  children: ReactNode
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
        {description ? <CardDescription className="text-xs">{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>
        <figure aria-label={title} className="flex flex-col gap-2">
          <div role="img" aria-label={summary}>
            {children}
          </div>
          <details className="text-xs">
            <summary className="w-fit cursor-pointer text-muted-foreground hover:text-foreground">Data table</summary>
            <div className="mt-2 max-h-64 overflow-auto">{table}</div>
          </details>
        </figure>
      </CardContent>
    </Card>
  )
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: Tone }) {
  return (
    <p className={cn("rounded-md border px-3 py-2 text-xs", TONE_CLASS[tone])} role="note">
      {children}
    </p>
  )
}

const OUTCOMES: Record<string, { label: string; tone: Tone; icon: typeof Minus }> = {
  profit: { label: "Profit", tone: "success", icon: ArrowUpRight },
  loss: { label: "Loss", tone: "destructive", icon: ArrowDownRight },
  breakeven: { label: "Break-even", tone: "neutral", icon: Minus },
  open: { label: "Open · not final", tone: "info", icon: Minus },
  unknown: { label: "Unknown", tone: "warning", icon: CircleHelp },
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const o = OUTCOMES[outcome] ?? { label: words(outcome), tone: "neutral" as Tone, icon: Minus }
  const Icon = o.icon
  return (
    <Badge className={cn("text-xs font-semibold", TONE_CLASS[o.tone])}>
      <Icon aria-hidden />
      {o.label}
    </Badge>
  )
}

const STATUS_TONE: Record<string, Tone> = {
  accepted: "success",
  approved: "success",
  filled: "success",
  closed: "neutral",
  rejected: "destructive",
  cancelled: "neutral",
  pending: "warning",
  working: "info",
  submitted: "info",
  partially_filled: "warning",
  ordered: "info",
  lapsed: "neutral",
  no_card: "neutral",
  none: "neutral",
  not_applicable: "neutral",
  open: "info",
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge className={cn("text-xs", TONE_CLASS[STATUS_TONE[status] ?? "neutral"])}>{words(status)}</Badge>
}

export function SortHead({
  label,
  active,
  order,
  onSort,
  numeric = false,
}: {
  label: string
  active: boolean
  order: "asc" | "desc"
  onSort: () => void
  numeric?: boolean
}) {
  const Icon = !active ? ArrowUpDown : order === "asc" ? ArrowUp : ArrowDown
  return (
    <TableHead aria-sort={!active ? "none" : order === "asc" ? "ascending" : "descending"} className={numeric ? "text-right" : undefined}>
      <button
        type="button"
        onClick={onSort}
        className="inline-flex items-center gap-1 rounded-sm font-medium hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        {label}
        <Icon aria-hidden className="size-3" />
      </button>
    </TableHead>
  )
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(total, page * pageSize)
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
      <span aria-live="polite">
        {first}–{last} of {total}
      </span>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <span className="px-1 tabular-nums">
          Page {page} of {pages}
        </span>
        <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </nav>
  )
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">{children}</p>
}
