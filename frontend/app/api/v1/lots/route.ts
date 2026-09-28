import type { NextRequest } from "next/server"
import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { listLots } from "@/app/api/v1/_store"
import { isLotStatus, type LotListFilters } from "@/features/lots/types"

export async function GET(request: NextRequest) {
  const filters: LotListFilters = {}
  const status = request.nextUrl.searchParams.get("status")
  if (status) {
    if (!isLotStatus(status)) return invalid("Unknown lot status")
    filters.status = status
  }

  try {
    return Response.json({ items: listLots(filters) })
  } catch (error) {
    return asErrorResponse(error)
  }
}
