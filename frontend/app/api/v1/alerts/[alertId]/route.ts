import { asErrorResponse } from "@/app/api/v1/_http"
import { getAlert } from "@/app/api/v1/_store"

export const runtime = "nodejs"

export async function GET(
  _request: Request,
  context: { params: Promise<{ alertId: string }> },
) {
  try {
    const { alertId } = await context.params
    return Response.json(getAlert(alertId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
