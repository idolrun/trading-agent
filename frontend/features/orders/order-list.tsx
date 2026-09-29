"use client"

import Link from "next/link"
import { useState } from "react"
import { Badge } from "@/components/badge"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { useOrderList } from "@/features/orders/queries"
import { ORDER_STATUSES, type OrderStatus } from "@/features/orders/types"

const FIELD =
  "h-9 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

/** `compact` is the desk panel: adds its own heading and a link to the full page. */
export function OrderList({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<OrderStatus | "">("")
  const query = useOrderList({ status: status || undefined })

  return (
    <section className="flex flex-col gap-3">
      {compact ? (
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-lg font-medium">Orders</h2>
          <Link href="/orders" className="text-sm underline underline-offset-2">
            All orders
          </Link>
        </div>
      ) : null}
      <label className="flex w-fit flex-col gap-1 text-sm">
        Status
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as OrderStatus | "")}
          className={FIELD}
        >
          <option value="">All</option>
          {ORDER_STATUSES.map((value) => (
            <option key={value} value={value}>
              {words(value)}
            </option>
          ))}
        </select>
      </label>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading orders"
        emptyTitle="No orders"
        emptyDescription={status ? "No orders match this status." : "An approved card shows up here as an order intent."}
      >
        {(data) => (
          <>
            <ul className="flex flex-col gap-2">
              {data.items.map((order) => (
                <li
                  key={order.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm"
                >
                  <Link href={`/orders/${order.id}`} className="underline underline-offset-2 capitalize">
                    {order.kind}
                  </Link>
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge>{order.mode}</Badge>
                    {order.autoReason ? <Badge>{`Auto: ${words(order.autoReason)}`}</Badge> : null}
                    <span className="text-muted-foreground capitalize">{order.status.replaceAll("_", " ")}</span>
                  </span>
                </li>
              ))}
            </ul>
            <LoadMore query={query} />
          </>
        )}
      </QueryState>
    </section>
  )
}
