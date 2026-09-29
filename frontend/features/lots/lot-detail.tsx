"use client"

import Link from "next/link"
import type { ReactNode } from "react"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { useLot } from "@/features/lots/queries"
import { AVG_LABEL, type LotDetail as Lot } from "@/features/lots/types"
import { formatContract, formatPrice, formatTime } from "@/lib/format"

/** `actions` is the slot for manual lot actions (F059); a foreign lot never gets them. */
export function LotView({ lot, actions }: { lot: Lot; actions?: ReactNode }) {
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-1 rounded-lg border border-border p-4 text-sm">
        <p className="flex flex-wrap items-center gap-2 text-base font-medium">
          {formatContract(lot.contract)}
          {lot.isForeign ? <Badge>Foreign</Badge> : null}
        </p>
        <p className="text-muted-foreground">
          <span className="capitalize">{lot.status}</span> · opened {formatTime(lot.openedAt)}
          {lot.providerPositionId ? (
            <>
              {" · "}
              <Link href={`/positions/${lot.providerPositionId}`} className="underline underline-offset-2">
                Provider position
              </Link>
            </>
          ) : null}
        </p>
        <p className="tabular-nums">Qty {lot.qty}</p>
        <p className="tabular-nums">
          Avg {formatPrice(lot.avgPrice)} <span className="text-muted-foreground">({AVG_LABEL})</span>
        </p>
        {lot.isForeign ? (
          <p className="mt-1 text-muted-foreground">
            Read-only. This lot was opened outside this system, so it is shown for reference and cannot be managed
            here.
          </p>
        ) : null}
      </section>

      {!lot.isForeign && actions ? <section aria-label="Actions">{actions}</section> : null}

      <section aria-label="Stops" className="flex flex-col gap-2">
        <h2 className="text-lg font-medium">Stops and take profits</h2>
        {lot.stops.length === 0 ? (
          <p className="text-sm text-muted-foreground">None set.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {lot.stops.map((stop) => (
              <li key={stop.id} className="flex flex-wrap justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
                <span>
                  {words(stop.kind)} at <span className="tabular-nums">{stop.level}</span> × {stop.qty}
                </span>
                <span className="text-muted-foreground">
                  {words(stop.basis)} · {words(stop.heldBy)} · {words(stop.status)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Orders" className="flex flex-col gap-2">
        <h2 className="text-lg font-medium">Orders</h2>
        {lot.orders.length === 0 ? (
          <p className="text-sm text-muted-foreground">No orders yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {lot.orders.map((order) => (
              <li key={order.id} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm">
                <Link href={`/orders/${order.id}`} className="underline underline-offset-2 capitalize">
                  {order.kind}
                </Link>
                <span className="text-muted-foreground capitalize">{order.status.replaceAll("_", " ")}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

export function LotDetail({ lotId }: { lotId: string }) {
  const query = useLot(lotId)
  return (
    <QueryState query={query} loadingLabel="Loading lot" emptyTitle="Lot not found">
      {(lot) => <LotView lot={lot} />}
    </QueryState>
  )
}
