"use client"

import { Badge } from "@/components/badge"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { useConfigChanges } from "@/features/config/queries"
import { formatTime } from "@/lib/format"

/** Newest first, from `GET /config/changes`. */
export function ConfigHistory() {
  const query = useConfigChanges()
  return (
    <section aria-labelledby="config-history" className="flex flex-col gap-3">
      <h2 id="config-history" className="text-lg font-medium">
        History
      </h2>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading history"
        emptyTitle="No changes yet"
      >
        {(data) => (
          <>
            <ol className="flex flex-col gap-2">
              {data.items.map((change) => (
                <li key={change.version} className="flex flex-col gap-1 rounded-lg border border-border p-3 text-sm">
                  <p className="font-medium">
                    Version {change.version} · {change.actor} · <time dateTime={change.at}>{formatTime(change.at)}</time>
                  </p>
                  {change.reason ? <p>{change.reason}</p> : null}
                  <p className="flex flex-wrap gap-1">
                    {Object.keys(change.diff).map((key) => (
                      <Badge key={key} className="font-mono">
                        {key}
                      </Badge>
                    ))}
                  </p>
                </li>
              ))}
            </ol>
            <LoadMore query={query} />
          </>
        )}
      </QueryState>
    </section>
  )
}
