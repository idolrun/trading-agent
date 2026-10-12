"use client"

import { TriangleAlert } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { orderStateLabel, TONE_CLASS, money } from "@/features/cards/card-state"
import { words } from "@/features/cards/types"
import { useOrderList } from "@/features/orders/queries"
import { ORDER_STATUSES, type Order, type OrderStatus } from "@/features/orders/types"
import { formatStamp } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Rows shown in the desk panel; the rest are one click away on the orders page. */
const COMPACT_ROWS = 6

/**
 * One order: contract, side and size, the broker-side status, and how much has filled. The status
 * is the order's own (submitted is not filled); a row with an unknown status says what to do.
 */
export function OrderRow({ order }: { order: Order }) {
  const state = orderStateLabel(order)
  const unknown = order.status === "unknown"
  const limits = [
    order.limitPrice ? `limit ${money(order.limitPrice)}` : null,
    order.stopPrice ? `stop ${money(order.stopPrice)}` : null,
  ].filter(Boolean)

  return (
    <li
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        unknown ? "border-warning/60 bg-warning-muted/30" : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-baseline gap-2">
          <Link href={`/orders/${order.id}`} className="font-medium capitalize underline-offset-2 hover:underline">
            {order.kind}
          </Link>
          <span className="truncate font-mono text-xs text-muted-foreground" title={order.brokerSymbol}>
            {order.brokerSymbol}
          </span>
        </span>
        <span
          className={cn("shrink-0 rounded-md border px-1.5 py-0.5 text-xs font-medium", TONE_CLASS[state.tone])}
        >
          {state.label}
        </span>
      </div>
      <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground tabular-nums">
        <span className="font-medium text-foreground uppercase">{order.side}</span>
        <span>{order.qty} qty</span>
        <span>
          filled {order.filledQty}/{order.qty}
        </span>
        {limits.length > 0 ? <span>{limits.join(" · ")}</span> : null}
        <span className="uppercase">{order.mode}</span>
        {order.autoReason ? <span>Auto: {words(order.autoReason)}</span> : null}
        <time dateTime={order.createdAt}>{formatStamp(order.createdAt)}</time>
      </p>
      {unknown ? (
        <p className="mt-1 flex items-center gap-1.5 text-xs text-warning-foreground">
          <TriangleAlert aria-hidden className="size-3.5" />
          Broker status unknown. Check the broker before assuming it filled or failed.
        </p>
      ) : null}
    </li>
  )
}

/** `compact` is the desk panel: adds its own heading, drops the filter, caps the rows. */
export function OrderList({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<OrderStatus | "">("")
  const query = useOrderList({ status: status || undefined })

  return (
    <section aria-labelledby={compact ? "orders-heading" : undefined} className="flex flex-col gap-2">
      {compact ? (
        <div className="flex items-baseline justify-between gap-3">
          <h2 id="orders-heading" className="text-sm font-semibold tracking-wide uppercase">
            Orders
            {query.data ? (
              <span className="ml-1.5 font-normal text-muted-foreground tabular-nums">{query.data.items.length}</span>
            ) : null}
          </h2>
          <Link href="/orders" className="text-xs underline underline-offset-2">
            Order history
          </Link>
        </div>
      ) : (
        <label className="flex w-fit flex-col gap-1 text-sm">
          Status
          <NativeSelect value={status} onChange={(event) => setStatus(event.target.value as OrderStatus | "")}>
            <NativeSelectOption value="">All</NativeSelectOption>
            {ORDER_STATUSES.map((value) => (
              <NativeSelectOption key={value} value={value}>
                {words(value)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
      )}
      <QueryState
        query={query}
        compact={compact}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading orders"
        emptyTitle="No orders"
        emptyDescription={status ? "None match this status" : "An approved card appears here as an order"}
      >
        {(data) => {
          const rows = compact ? data.items.slice(0, COMPACT_ROWS) : data.items
          return (
            <>
              <ul className="flex flex-col gap-1.5">
                {rows.map((order) => (
                  <OrderRow key={order.id} order={order} />
                ))}
              </ul>
              {compact && data.items.length > rows.length ? (
                <Link href="/orders" className="text-xs underline underline-offset-2">
                  {data.items.length - rows.length} more in order history
                </Link>
              ) : (
                <LoadMore query={query} />
              )}
            </>
          )
        }}
      </QueryState>
    </section>
  )
}
