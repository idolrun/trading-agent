"use client"

import { Badge } from "@/components/badge"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { useConfigChanges } from "@/features/config/queries"
import { formatTime } from "@/lib/format"

const show = (value: unknown) => JSON.stringify(value) ?? "unset"
const ACTORS: Record<string, string> = { user: "User", cli: "CLI", system: "System" }

/** Newest first, from `GET /config/changes`. Before/after are the values at that version, not the current ones. */
export function ConfigHistory() {
  const query = useConfigChanges()
  return (
    <section id="history" aria-labelledby="config-history" className="flex scroll-mt-4 flex-col gap-3">
      <h2 id="config-history" className="text-lg font-medium">
        History
      </h2>
      <p className="text-xs text-muted-foreground">
        Every saved version. Values shown are what changed at that version, not necessarily what is active now.
      </p>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading history"
        emptyTitle="No changes yet"
      >
        {(data) => (
          <>
            <ol className="divide-y divide-border rounded-lg border border-border">
              {data.items.map((change) => {
                const keys = Object.keys(change.diff)
                const diff = change.diff as Record<string, { before?: unknown; after?: unknown }>
                return (
                  <li key={change.version} className="p-3 text-sm">
                    <details className="group">
                      <summary className="flex min-h-9 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 rounded focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                        <span className="font-medium tabular-nums">Version {change.version}</span>
                        <Badge>{ACTORS[change.actor] ?? change.actor}</Badge>
                        <time dateTime={change.at} className="text-xs text-muted-foreground">
                          {formatTime(change.at)}
                        </time>
                        <span className="text-xs text-muted-foreground">
                          {keys.length} {keys.length === 1 ? "setting" : "settings"} changed · <span className="underline group-open:hidden">View details</span>
                          <span className="hidden underline group-open:inline">Hide details</span>
                        </span>
                        {change.reason ? <span className="basis-full break-words">{change.reason}</span> : null}
                      </summary>
                      <dl className="mt-2 flex flex-col gap-2">
                        {keys.map((key) => (
                          <div key={key} className="grid gap-1 rounded-md bg-muted/50 p-2 sm:grid-cols-[minmax(0,14rem)_1fr]">
                            <dt className="font-mono text-xs break-all">{key}</dt>
                            <dd className="grid gap-1 text-xs sm:grid-cols-2">
                              <span className="font-mono break-all">
                                <span className="font-sans text-muted-foreground">Before: </span>
                                {show(diff[key]?.before)}
                              </span>
                              <span className="font-mono break-all">
                                <span className="font-sans text-muted-foreground">After: </span>
                                {show(diff[key]?.after)}
                              </span>
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </details>
                  </li>
                )
              })}
            </ol>
            <LoadMore query={query} />
          </>
        )}
      </QueryState>
    </section>
  )
}
