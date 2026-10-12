"use client"

import { Ban, FlaskConical, Radio, ShieldCheck, ShieldQuestion, Eye } from "lucide-react"
import { useEntryStatus, useTradingMode, type TradingMode } from "@/features/health/trading-state"
import { cn } from "@/lib/utils"

const MODE: Record<TradingMode | "unknown", { text: string; hint: string; icon: typeof Eye; style: string }> = {
  shadow: {
    text: "SHADOW",
    hint: "Shadow mode: nothing is sent to the broker",
    icon: Eye,
    style: "border-border bg-muted text-foreground",
  },
  paper: {
    text: "PAPER",
    hint: "Paper mode: orders go to the paper account",
    icon: FlaskConical,
    style: "border-info/50 bg-info-muted text-info-foreground",
  },
  live: {
    text: "LIVE",
    hint: "Live mode: orders use real money",
    icon: Radio,
    style: "border-destructive bg-destructive font-bold text-background ring-2 ring-destructive/40",
  },
  unknown: {
    text: "MODE ?",
    hint: "Trading mode could not be read",
    icon: ShieldQuestion,
    style: "border-warning/50 bg-warning-muted text-warning-foreground",
  },
}

/** Trading mode, as text plus an icon; LIVE is also filled and ringed so colour is not the only cue. */
export function ModeBadge() {
  const mode = useTradingMode()
  const { text, hint, icon: Icon, style } = MODE[mode]
  return (
    <span
      role="status"
      aria-label={`Trading mode: ${text}. ${hint}`}
      title={hint}
      data-mode={mode}
      className={cn("inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs tracking-wider font-semibold", style)}
    >
      <Icon aria-hidden className="size-3.5" />
      {text}
    </span>
  )
}

/** Whether new entries can open: the answer to "can this desk start anything right now?". */
export function EntriesBadge() {
  const { state, reasons } = useEntryStatus()
  if (state === "loading") {
    return (
      <span className="inline-flex h-7 items-center rounded-md border border-border px-2 text-xs text-muted-foreground">
        Entries …
      </span>
    )
  }
  const blocked = state === "blocked"
  const Icon = blocked ? Ban : ShieldCheck
  return (
    <span
      role="status"
      title={blocked ? reasons.join(". ") : "New entries are allowed"}
      data-entries={state}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs font-semibold",
        blocked
          ? "border-destructive/60 bg-destructive/15 text-destructive"
          : "border-success/40 bg-success-muted text-success-foreground",
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {blocked ? "Entries blocked" : "Entries enabled"}
    </span>
  )
}
