import type { NextRequest } from "next/server"
import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { listCards } from "@/app/api/v1/_store"
import { isCardState, type CardListFilters } from "@/features/cards/types"

export async function GET(request: NextRequest) {
  const filters: CardListFilters = {}
  const state = request.nextUrl.searchParams.get("state")
  if (state) {
    if (!isCardState(state)) return invalid("Unknown card state")
    filters.state = state
  }
  const providerId = request.nextUrl.searchParams.get("providerId")
  if (providerId) filters.providerId = providerId

  try {
    return Response.json({ items: listCards(filters) })
  } catch (error) {
    return asErrorResponse(error)
  }
}
