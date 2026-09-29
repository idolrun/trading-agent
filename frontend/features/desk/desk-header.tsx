"use client"

import { ConnectionIndicator } from "@/features/live/connection-indicator"
import { useLiveEvents } from "@/features/live/use-live-events"
import { PushToggle } from "@/features/push/push-toggle"

/** Rendered once, inside the session gate: it owns the SSE connection for the whole desk. */
export function DeskHeader() {
  const status = useLiveEvents()
  return (
    <>
      <div className="mr-auto">
        <ConnectionIndicator status={status} />
      </div>
      <PushToggle />
    </>
  )
}
