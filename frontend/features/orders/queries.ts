import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { OrderDetail, OrderListFilters } from "@/features/orders/types"

export const orderKeys = domainKeys("orders")

export type OrderPage = Schemas["Page_OrderOut_"]

export function fetchOrderPage(filters: OrderListFilters, cursor?: string): Promise<OrderPage> {
  return apiGet<OrderPage>("/api/v1/orders", { ...filters, cursor })
}

export function fetchOrder(orderId: string): Promise<OrderDetail> {
  return apiGet<OrderDetail>(`/api/v1/orders/${encodeURIComponent(orderId)}`)
}

/** "Load more" follows `nextCursor`; a refetch (SSE, poll) reloads every loaded page. */
export function orderListQuery(filters: OrderListFilters) {
  return infiniteQueryOptions({
    queryKey: orderKeys.list(filters),
    queryFn: ({ pageParam }) => fetchOrderPage(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })
}

export function orderDetailQuery(orderId: string) {
  return queryOptions({
    queryKey: orderKeys.detail(orderId),
    queryFn: () => fetchOrder(orderId),
    enabled: orderId.length > 0,
  })
}

export function useOrderList(filters: OrderListFilters) {
  return useInfiniteQuery({
    ...orderListQuery(filters),
    select: (data) => ({ items: data.pages.flatMap((page) => page.items) }),
  })
}

export function useOrder(orderId: string) {
  return useQuery(orderDetailQuery(orderId))
}
