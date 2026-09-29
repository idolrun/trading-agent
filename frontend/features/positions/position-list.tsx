"use client"

import Link from "next/link"
import { useState } from "react"
import { Badge } from "@/components/badge"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { usePositionList } from "@/features/positions/queries"
import { POSITION_STATUSES, type PositionStatus, type ProviderPosition } from "@/features/positions/types"
import { formatContract } from "@/lib/format"

const FIELD =
  "h-9 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

export const positionLabel = (p: ProviderPosition) => formatContract({ ...p, right: p.optionRight })

/** Provider ids are snowflakes: string keys only, first-seen order. */
function groupByProvider(items: ProviderPosition[]) {
  const groups = new Map<string, ProviderPosition[]>()
  for (const item of items) groups.set(item.providerId, [...(groups.get(item.providerId) ?? []), item])
  return [...groups]
}

export function PositionList() {
  const [status, setStatus] = useState<PositionStatus | "">("")
  const query = usePositionList({ status: status || undefined })

  return (
    <section className="flex flex-col gap-3">
      <label className="flex w-fit flex-col gap-1 text-sm">
        Status
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as PositionStatus | "")}
          className={FIELD}
        >
          <option value="">All</option>
          {POSITION_STATUSES.map((value) => (
            <option key={value} value={value}>
              {words(value)}
            </option>
          ))}
        </select>
      </label>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading positions"
        emptyTitle="No provider positions"
        emptyDescription={status ? "No positions match this status." : "Positions show up here once a provider opens one."}
      >
        {(data) => (
          <>
            {groupByProvider(data.items).map(([providerId, positions]) => (
              <section key={providerId} aria-label={`Provider ${providerId}`} className="flex flex-col gap-2">
                <h2 className="text-sm font-medium text-muted-foreground">Provider {providerId}</h2>
                <ul className="flex flex-col gap-2">
                  {positions.map((position) => (
                    <li
                      key={position.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm"
                    >
                      <Link href={`/positions/${position.id}`} className="underline underline-offset-2">
                        {positionLabel(position)}
                      </Link>
                      <span className="flex items-center gap-2">
                        {position.isCorrected ? <Badge>Corrected</Badge> : null}
                        <span className="text-muted-foreground capitalize">{position.status}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <LoadMore query={query} />
          </>
        )}
      </QueryState>
    </section>
  )
}
