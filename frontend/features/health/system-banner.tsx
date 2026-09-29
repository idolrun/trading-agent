"use client"

import { useHealthDetails } from "@/features/health/queries"
import { cn } from "@/lib/utils"

type Level = "urgent" | "info"

// The backend exposes booleans only (no reason or since), so the text is static per flag.
const FLAGS: Record<string, { level: Level; text: string }> = {
  kill_switch: { level: "urgent", text: "Kill switch is on. No new orders are being sent." },
  entries_blocked: { level: "urgent", text: "New entries are blocked." },
  feed_degraded: { level: "urgent", text: "Feed degraded. Messages may be missing or late." },
  catch_up: { level: "info", text: "Catching up on a message backlog. Cards may be late." },
  parser_degraded: { level: "info", text: "Parser degraded. Some messages may need manual review." },
}

const STYLE: Record<Level, string> = {
  urgent: "border-destructive/40 bg-destructive/10 text-destructive",
  info: "border-border bg-muted text-foreground",
}

/** Global degraded-state strip. Renders nothing while every flag is clear (or health has not loaded). */
export function SystemBanner() {
  const { data } = useHealthDetails("session")
  const active = Object.entries(FLAGS).filter(([flag]) => data?.flags[flag])
  if (active.length === 0) return null

  return (
    <div className="flex flex-col gap-px">
      {active.map(([flag, { level, text }]) => (
        <p
          key={flag}
          data-flag={flag}
          role={level === "urgent" ? "alert" : "status"}
          className={cn("border-b px-4 py-2 text-sm font-medium sm:px-6", STYLE[level])}
        >
          {text}
        </p>
      ))}
    </div>
  )
}
