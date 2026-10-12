"use client"

import { ConnectionIndicator } from "@/features/live/connection-indicator"
import { FlattenControl } from "@/features/manual/flatten-dialog"
import { KillSwitch } from "@/features/manual/kill-switch"
import { useLiveEvents } from "@/features/live/use-live-events"
import { EntriesBadge, ModeBadge } from "@/features/health/trading-badges"
import { PushToggle } from "@/features/push/push-toggle"

/** Rendered once, inside the session gate: it owns the SSE connection for the whole desk. */
export function DeskHeader() {
  const status = useLiveEvents()
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="font-heading text-sm font-semibold tracking-tight">Desk</span>
      <ModeBadge />
      <EntriesBadge />
      <ConnectionIndicator status={status} />
    </div>
  )
}

/** Kill switch and Flatten stay in the bar on every width: they are the emergency controls. */
export function DeskControls() {
  return (
    <div role="group" aria-label="Emergency controls" className="flex items-center gap-2">
      <KillSwitch />
      <FlattenControl />
    </div>
  )
}

export function DeskMenu() {
  return <PushToggle />
}
