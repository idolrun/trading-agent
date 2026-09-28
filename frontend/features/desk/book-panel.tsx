"use client"

import { QueryState } from "@/components/query-state"
import { formatPrice } from "@/lib/format"
import { useLotList } from "@/features/lots/queries"
import { usePositionList } from "@/features/positions/queries"

export function BookPanel() {
  const lots = useLotList({ status: "open" })
  const positions = usePositionList({})

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-lg font-medium">Book</h2>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-muted-foreground">Lots</h3>
        <QueryState
          query={lots}
          isEmpty={(data) => data.items.length === 0}
          loadingLabel="Loading lots"
          emptyTitle="No open lots"
        >
          {(data) => (
            <ul className="flex flex-col gap-2">
              {data.items.map((lot) => (
                <li
                  key={lot.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm"
                >
                  <span>{lot.contract}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    {lot.qty} @ {formatPrice(lot.avgPrice)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-muted-foreground">Provider positions</h3>
        <QueryState
          query={positions}
          isEmpty={(data) => data.items.length === 0}
          loadingLabel="Loading positions"
          emptyTitle="No provider positions"
        >
          {(data) => (
            <ul className="flex flex-col gap-2">
              {data.items.map((position) => (
                <li
                  key={position.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm"
                >
                  <span>
                    {position.underlying}
                    {position.optionRight ? ` ${position.optionRight}` : ""}
                  </span>
                  <span className="capitalize text-muted-foreground">{position.status}</span>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </div>
    </section>
  )
}
