import { asErrorResponse } from "@/app/api/v1/_http"
import { getPosition } from "@/app/api/v1/_store"

export async function GET(
  _request: Request,
  context: { params: Promise<{ positionId: string }> },
) {
  try {
    const { positionId } = await context.params
    return Response.json(getPosition(positionId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
