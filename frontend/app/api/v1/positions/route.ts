import type { NextRequest } from "next/server"
import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { listPositions } from "@/app/api/v1/_store"
import { isPositionStatus, type PositionListFilters } from "@/features/positions/types"

export async function GET(request: NextRequest) {
  const filters: PositionListFilters = {}
  const status = request.nextUrl.searchParams.get("status")
  if (status) {
    if (!isPositionStatus(status)) return invalid("Unknown position status")
    filters.status = status
  }
  const providerId = request.nextUrl.searchParams.get("providerId")
  if (providerId) filters.providerId = providerId

  try {
    return Response.json({ items: listPositions(filters) })
  } catch (error) {
    return asErrorResponse(error)
  }
}
