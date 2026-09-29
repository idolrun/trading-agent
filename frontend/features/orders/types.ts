import type { ListFilters, Schemas } from "@/lib/api/types"

export type Order = Schemas["OrderOut"]
export type OrderDetail = Schemas["OrderDetail"]
export type OrderStatus = Schemas["OrderStatus"]
export type OrderListFilters = ListFilters<"list_orders_api_v1_orders_get">
export const ORDER_STATUSES: OrderStatus[] = [
  "intent",
  "submitted",
  "working",
  "partially_filled",
  "filled",
  "cancelled",
  "rejected",
  "unknown",
]
