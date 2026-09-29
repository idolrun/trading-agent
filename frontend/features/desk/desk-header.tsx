"use client"

import { DeskNav } from "@/features/desk/desk-nav"
import { ConnectionIndicator } from "@/features/live/connection-indicator"
import { useLiveEvents } from "@/features/live/use-live-events"
import { PushToggle } from "@/features/push/push-toggle"

/** Rendered once, inside the session gate: it owns the SSE connection for the whole desk. */
export function DeskHeader() {
  const status = useLiveEvents()
  return (
    <>
      <div className="mr-auto flex flex-wrap items-center gap-x-4 gap-y-1">
        <DeskNav />
        <ConnectionIndicator status={status} />
      </div>
      <PushToggle />
    </>
  )
}
