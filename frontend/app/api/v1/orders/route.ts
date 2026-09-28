import type { NextRequest } from "next/server"
import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { listOrders } from "@/app/api/v1/_store"
import { isOrderStatus, type OrderListFilters } from "@/features/orders/types"

export async function GET(request: NextRequest) {
  const filters: OrderListFilters = {}
  const status = request.nextUrl.searchParams.get("status")
  if (status) {
    if (!isOrderStatus(status)) return invalid("Unknown order status")
    filters.status = status
  }
  const lotId = request.nextUrl.searchParams.get("lotId")
  if (lotId) filters.lotId = lotId

  try {
    return Response.json({ items: listOrders(filters) })
  } catch (error) {
    return asErrorResponse(error)
  }
}
