"use client"

import Link from "next/link"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { useOrder } from "@/features/orders/queries"
import { formatPrice, formatTime } from "@/lib/format"

export function OrderDetail({ orderId }: { orderId: string }) {
  const query = useOrder(orderId)

  return (
    <QueryState query={query} loadingLabel="Loading order" emptyTitle="Order not found">
      {(order) => (
        <div className="flex flex-col gap-6">
          <section className="flex flex-col gap-1 rounded-lg border border-border p-4 text-sm">
            <p className="flex flex-wrap items-center gap-2 text-base font-medium">
              <span className="capitalize">{order.kind}</span> {order.side} {order.brokerSymbol}
              <Badge>{order.mode}</Badge>
              {order.autoReason ? <Badge>{`Auto: ${words(order.autoReason)}`}</Badge> : null}
            </p>
            <p className="tabular-nums">
              {order.filledQty} of {order.qty} filled · limit {formatPrice(order.limitPrice)}
              {order.stopPrice ? ` · stop ${formatPrice(order.stopPrice)}` : ""}
            </p>
            <p className="text-muted-foreground">
              <span className="capitalize">{order.status.replaceAll("_", " ")}</span> · created{" "}
              {formatTime(order.createdAt)}
            </p>
            <p className="flex gap-4">
              {order.cardId ? (
                <Link href={`/cards/${order.cardId}`} className="underline underline-offset-2">
                  Card
                </Link>
              ) : null}
              {order.lotId ? (
                <Link href={`/lots/${order.lotId}`} className="underline underline-offset-2">
                  Lot
                </Link>
              ) : null}
            </p>
          </section>

          <section aria-label="State history" className="flex flex-col gap-2">
            <h2 className="text-lg font-medium">State history</h2>
            {order.events.length === 0 ? (
              <p className="text-sm text-muted-foreground">No state changes recorded.</p>
            ) : (
              <ol className="flex flex-col gap-2">
                {order.events.map((event, index) => (
                  <li key={`${event.at}:${index}`} className="flex flex-wrap items-baseline justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
                    <span>
                      <span className="font-medium capitalize">{event.status.replaceAll("_", " ")}</span>{" "}
                      <span className="text-muted-foreground">via {event.source}</span>
                    </span>
                    <time dateTime={event.at} className="text-xs text-muted-foreground tabular-nums">
                      {formatTime(event.at)}
                    </time>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-label="Fills" className="flex flex-col gap-2">
            <h2 className="text-lg font-medium">Fills</h2>
            {order.fills.length === 0 ? (
              <p className="text-sm text-muted-foreground">No fills yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {order.fills.map((fill) => (
                  <li key={fill.id} className="flex flex-wrap justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
                    <span className="tabular-nums">
                      {fill.qty} @ {formatPrice(fill.price)}
                    </span>
                    <time dateTime={fill.filledAt} className="text-xs text-muted-foreground tabular-nums">
                      {formatTime(fill.filledAt)}
                    </time>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </QueryState>
  )
}
