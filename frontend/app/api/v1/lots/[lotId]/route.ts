import { asErrorResponse } from "@/app/api/v1/_http"
import { getLot } from "@/app/api/v1/_store"

export async function GET(
  _request: Request,
  context: { params: Promise<{ lotId: string }> },
) {
  try {
    const { lotId } = await context.params
    return Response.json(getLot(lotId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
