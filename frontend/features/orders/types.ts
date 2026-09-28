export const ORDER_STATUSES = [
  "intent",
  "submitted",
  "working",
  "partially_filled",
  "filled",
  "cancelled",
  "rejected",
  "unknown",
] as const

export type OrderStatus = (typeof ORDER_STATUSES)[number]

export const ORDER_KINDS = ["entry", "exit", "stop", "tp"] as const

export type OrderKind = (typeof ORDER_KINDS)[number]

export type Order = {
  id: string
  cardId: string | null
  lotId: string | null
  kind: OrderKind
  status: OrderStatus
  clientOrderId: string
}

export type OrderListFilters = {
  status?: OrderStatus
  lotId?: string
}

export function isOrderStatus(value: string | null): value is OrderStatus {
  return ORDER_STATUSES.some((status) => status === value)
}
