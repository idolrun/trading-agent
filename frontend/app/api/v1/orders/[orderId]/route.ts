import { asErrorResponse } from "@/app/api/v1/_http"
import { getOrder } from "@/app/api/v1/_store"

export async function GET(
  _request: Request,
  context: { params: Promise<{ orderId: string }> },
) {
  try {
    const { orderId } = await context.params
    return Response.json(getOrder(orderId))
  } catch (error) {
    return asErrorResponse(error)
  }
}
