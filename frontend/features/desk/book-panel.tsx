"use client"

import Link from "next/link"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { formatContract, formatPrice } from "@/lib/format"
import { useLotList } from "@/features/lots/queries"
import { AVG_LABEL } from "@/features/lots/types"
import { usePositionList } from "@/features/positions/queries"

export function BookPanel() {
  const lots = useLotList({ status: "open" })
  const positions = usePositionList({})

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold tracking-wide uppercase">Book</h2>
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-xs font-medium text-muted-foreground">Open lots</h3>
          <Link href="/positions" className="text-xs underline underline-offset-2">
            All positions
          </Link>
        </div>
        <p className="text-xs text-muted-foreground">Average price is the {AVG_LABEL}.</p>
        <QueryState
          compact
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
                  <span className="flex items-center gap-2">
                    <Link href={`/lots/${lot.id}`} className="underline underline-offset-2">
                      {formatContract(lot.contract)}
                    </Link>
                    {lot.isForeign ? <Badge>Foreign</Badge> : null}
                  </span>
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
        <h3 className="text-xs font-medium text-muted-foreground">Provider positions</h3>
        <QueryState
          compact
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
                  <Link href={`/positions/${position.id}`} className="underline underline-offset-2">
                    {position.underlying}
                    {position.optionRight ? ` ${position.optionRight}` : ""}
                  </Link>
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
