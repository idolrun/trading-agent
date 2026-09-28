import { asErrorResponse } from "@/app/api/v1/_http"
import { acknowledgeAlert } from "@/app/api/v1/_store"

export async function POST(
  _request: Request,
  context: { params: Promise<{ alertId: string }> },
) {
  try {
    const { alertId } = await context.params
    return Response.json(acknowledgeAlert(alertId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
