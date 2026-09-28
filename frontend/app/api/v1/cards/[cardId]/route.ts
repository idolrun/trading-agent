import { asErrorResponse } from "@/app/api/v1/_http"
import { getCard } from "@/app/api/v1/_store"

export async function GET(
  _request: Request,
  context: { params: Promise<{ cardId: string }> },
) {
  try {
    const { cardId } = await context.params
    return Response.json(getCard(cardId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
