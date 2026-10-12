"use client"

import { CircleCheck, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react"
import { useHealthDetails } from "@/features/health/queries"
import type { HealthDetails } from "@/features/health/types"
import { formatDuration, formatStamp } from "@/lib/format"
import { cn } from "@/lib/utils"

export type Level = "ok" | "warning" | "urgent"

export type StatusCell = {
  key: "safety" | "feed" | "parser"
  title: string
  level: Level
  /** The headline: what is true now. */
  headline: string
  /** Supporting facts the backend actually reports. */
  details: string[]
  /** Flags behind this cell, kept as data attributes so tests and e2e can find them. */
  flags: string[]
}

const worst = (a: Level, b: Level): Level => (a === "urgent" || b === "urgent" ? "urgent" : a === "warning" || b === "warning" ? "warning" : "ok")

function safetyCell(health: HealthDetails): StatusCell {
  const { flags, reconciliation: recon } = health
  const problems: { level: Level; text: string }[] = []
  const active: string[] = []
  if (flags.kill_switch) {
    active.push("kill_switch")
    problems.push({ level: "urgent", text: "Kill switch on: no new orders are sent" })
  }
  if (flags.entries_blocked) {
    active.push("entries_blocked")
    problems.push({ level: "urgent", text: "New entries blocked by the system" })
  }
  if (recon.clean === false || recon.mismatches > 0) {
    problems.push({ level: "urgent", text: `Reconciliation mismatch (${recon.mismatches || "see health"})` })
  } else if (recon.error) {
    problems.push({ level: "warning", text: `Reconciliation error: ${recon.error}` })
  }
  if (recon.unknownOrders > 0) {
    problems.push({
      level: "urgent",
      text: `${recon.unknownOrders} order${recon.unknownOrders === 1 ? "" : "s"} with unknown status: reconcile with the broker`,
    })
  }

  const level = problems.reduce<Level>((acc, p) => worst(acc, p.level), "ok")
  const reconNote =
    recon.lastRunAt == null
      ? "Reconciliation not run yet"
      : recon.clean
        ? `Reconciled ${formatStamp(recon.lastRunAt)}`
        : `Last reconciliation ${formatStamp(recon.lastRunAt)}`
  return {
    key: "safety",
    title: "Trading safety",
    level,
    headline: problems[0]?.text ?? "Kill switch off",
    details: [...problems.slice(1).map((p) => p.text), ...(level === "ok" || recon.clean !== false ? [reconNote] : [])],
    flags: active,
  }
}

function feedCell(health: HealthDetails): StatusCell {
  const degraded = health.flags.feed_degraded === true || health.status !== "ok"
  const catchUp = health.flags.catch_up === true || health.catchUp
  const details: string[] = []
  if (health.feedSilenceSeconds != null) details.push(`Last message ${formatDuration(health.feedSilenceSeconds)} ago`)
  else details.push("No messages yet")
  if (catchUp || health.backlogDepth > 0) {
    const oldest = health.oldestUnprocessedSeconds
    details.push(
      `Backlog ${health.backlogDepth}${oldest != null && health.backlogDepth > 0 ? `, oldest ${formatDuration(oldest)}` : ""}`,
    )
  }
  const flags = [degraded ? "feed_degraded" : null, catchUp ? "catch_up" : null].filter((f) => f != null)
  if (degraded) {
    return { key: "feed", title: "Feed", level: "urgent", headline: "Feed degraded. Messages may be missing or late.", details, flags }
  }
  if (catchUp) {
    return { key: "feed", title: "Feed", level: "warning", headline: "Catching up: cards may be late", details, flags }
  }
  return { key: "feed", title: "Feed", level: "ok", headline: "Healthy", details, flags }
}

function parserCell(health: HealthDetails): StatusCell {
  if (health.flags.parser_degraded) {
    return {
      key: "parser",
      title: "Parser",
      level: "warning",
      headline: "Degraded: manual review required",
      details: ["Some messages may need a person to read them"],
      flags: ["parser_degraded"],
    }
  }
  return { key: "parser", title: "Parser", level: "ok", headline: "Healthy", details: [], flags: [] }
}

export const statusCells = (health: HealthDetails): StatusCell[] => [safetyCell(health), feedCell(health), parserCell(health)]

const LEVEL: Record<Level, { icon: LucideIcon; cell: string; icon_: string; word: string }> = {
  ok: { icon: CircleCheck, cell: "border-border", icon_: "text-success", word: "OK" },
  warning: { icon: TriangleAlert, cell: "border-warning/60 bg-warning-muted/40", icon_: "text-warning", word: "Warning" },
  urgent: { icon: OctagonAlert, cell: "border-destructive/60 bg-destructive/10", icon_: "text-destructive", word: "Urgent" },
}

function Cell({ cell, allOk }: { cell: StatusCell; allOk: boolean }) {
  const { icon: Icon, cell: border, icon_: iconTone, word } = LEVEL[cell.level]
  const quiet = cell.level === "ok"
  return (
    <div
      role={cell.level === "urgent" ? "alert" : "group"}
      aria-label={`${cell.title}: ${word}`}
      data-cell={cell.key}
      data-level={cell.level}
      data-flags={cell.flags.join(" ") || undefined}
      className={cn(
        "flex min-w-0 rounded-md border",
        quiet ? "flex-row flex-wrap items-baseline gap-x-2 px-2.5 py-1" : "flex-col gap-0.5 px-3 py-2",
        // On a phone an all-clear strip is one row of three; any problem stacks them full width.
        allOk ? "col-span-1" : "col-span-3 md:col-span-1",
        border,
      )}
    >
      <p className="flex items-center gap-1.5 text-[11px] font-medium tracking-wider text-muted-foreground uppercase">
        <Icon aria-hidden className={cn("size-3.5 shrink-0", iconTone)} />
        {cell.title}
        {quiet ? null : <span className="sr-only">{word}</span>}
      </p>
      <p className={cn("text-sm leading-snug", quiet ? "text-foreground" : "font-medium")}>{cell.headline}</p>
      {cell.details.length > 0 ? (
        <p className="text-xs text-muted-foreground tabular-nums">{cell.details.join(" · ")}</p>
      ) : null}
    </div>
  )
}

/**
 * One strip for the three things that can stop the desk: trading safety, feed, parser. Each
 * incident appears once, in its own cell; the header carries the mode and the entries verdict.
 * An unresolved safety condition stays here until the backend clears it. If health cannot be
 * read, that is shown too: an absent strip would look like "all clear".
 */
export function OperationalStatus() {
  const query = useHealthDetails("session")

  if (!query.data) {
    return (
      <div className="px-4 pt-3 sm:px-6">
        {query.isError ? (
          <p role="alert" className="rounded-md border border-warning/60 bg-warning-muted/40 px-3 py-2 text-sm">
            Health status unavailable. Treat feed and safety state as unknown.{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => void query.refetch()}>
              Retry
            </button>
          </p>
        ) : (
          <p role="status" className="px-1 text-xs text-muted-foreground">
            Checking system status…
          </p>
        )}
      </div>
    )
  }

  const cells = statusCells(query.data)
  const allOk = cells.every((cell) => cell.level === "ok")
  return (
    <section aria-label="Operational status" className="grid grid-cols-3 gap-2 px-4 pt-3 sm:px-6">
      {cells.map((cell) => (
        <Cell key={cell.key} cell={cell} allOk={allOk} />
      ))}
    </section>
  )
}
