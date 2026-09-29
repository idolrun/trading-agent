"use client"

import { QueryState } from "@/components/query-state"
import { useHealthDetails } from "@/features/health/queries"

export function FeedStatus() {
  const query = useHealthDetails("session")

  return (
    <QueryState query={query} loadingLabel="Checking feed" emptyTitle="No feed status">
      {(health) => (
        <p className="text-sm">
          <span className="capitalize">{health.status === "ok" ? "Feed healthy" : "Feed degraded"}</span>
          <span className="text-muted-foreground">
            ·{" "}
            {health.feedSilenceSeconds == null
              ? "no messages yet"
              : `${health.feedSilenceSeconds}s since last message`}
          </span>
        </p>
      )}
    </QueryState>
  )
}
