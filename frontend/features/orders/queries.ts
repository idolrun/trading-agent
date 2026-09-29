import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { OrderDetail, OrderListFilters } from "@/features/orders/types"

export const orderKeys = domainKeys("orders")

export type OrderList = Schemas["Page_OrderOut_"]

export function fetchOrderList(filters: OrderListFilters): Promise<OrderList> {
  return apiGet<OrderList>("/api/v1/orders", filters)
}

export function fetchOrder(orderId: string): Promise<OrderDetail> {
  return apiGet<OrderDetail>(`/api/v1/orders/${encodeURIComponent(orderId)}`)
}

export function orderListQuery(filters: OrderListFilters) {
  return queryOptions({
    queryKey: orderKeys.list(filters),
    queryFn: () => fetchOrderList(filters),
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
  return useQuery(orderListQuery(filters))
}

export function useOrder(orderId: string) {
  return useQuery(orderDetailQuery(orderId))
}
