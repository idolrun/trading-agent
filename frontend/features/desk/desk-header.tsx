"use client"

import { ConnectionIndicator } from "@/features/live/connection-indicator"
import { FlattenControl } from "@/features/manual/flatten-dialog"
import { KillSwitch } from "@/features/manual/kill-switch"
import { useLiveEvents } from "@/features/live/use-live-events"
import { PushToggle } from "@/features/push/push-toggle"

/** Rendered once, inside the session gate: it owns the SSE connection for the whole desk. */
export function DeskHeader() {
  const status = useLiveEvents()
  return <ConnectionIndicator status={status} />
}

export function DeskControls() {
  return (
    <>
      <KillSwitch />
      <FlattenControl />
      <PushToggle />
    </>
  )
}
