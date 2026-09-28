import { StoreError } from "@/app/api/v1/_store"

export function invalid(message: string): Response {
  return Response.json({ message, code: "invalid" }, { status: 400 })
}

export function asErrorResponse(error: unknown): Response {
  if (error instanceof StoreError) {
    return Response.json(
      { message: error.message, code: error.code },
      { status: error.status },
    )
  }
  throw error
}
