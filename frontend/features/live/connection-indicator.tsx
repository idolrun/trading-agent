import type { LiveStatus } from "@/features/live/use-live-events"
import { cn } from "@/lib/utils"

const LABEL: Record<LiveStatus, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
}

export function ConnectionIndicator({ status }: { status: LiveStatus }) {
  return (
    <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        aria-hidden
        className={cn("size-2 rounded-full", status === "live" ? "bg-emerald-500" : "animate-pulse bg-amber-500")}
      />
      {LABEL[status]}
    </span>
  )
}
