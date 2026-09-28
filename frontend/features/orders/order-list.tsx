"use client"

import { QueryState } from "@/components/query-state"
import { useOrderList } from "@/features/orders/queries"

export function OrderList() {
  const query = useOrderList({})

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">Orders</h2>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading orders"
        emptyTitle="No orders"
        emptyDescription="An approved card shows up here as an order intent."
      >
        {(data) => (
          <ul className="flex flex-col gap-2">
            {data.items.map((order) => (
              <li
                key={order.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm"
              >
                <span className="capitalize">{order.kind}</span>
                <span className="text-muted-foreground capitalize">
                  {order.status.replaceAll("_", " ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </section>
  )
}
