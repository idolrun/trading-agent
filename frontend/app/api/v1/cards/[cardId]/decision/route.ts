import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { decideCard, StoreError } from "@/app/api/v1/_store"
import type { CardDecision } from "@/features/cards/types"

function readPositive(value: unknown, label: string): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new StoreError(`${label} must be a positive number`, 400, "invalid")
  }
  return value
}

function parseDecision(body: unknown): CardDecision {
  if (!body || typeof body !== "object") {
    throw new StoreError("Invalid decision", 400, "invalid")
  }
  const record = body as { decision?: unknown; edits?: unknown }
  if (record.decision === "approve" || record.decision === "reject") {
    return { decision: record.decision }
  }
  if (record.decision !== "edit") {
    throw new StoreError("Invalid decision", 400, "invalid")
  }
  if (record.edits !== undefined && (typeof record.edits !== "object" || record.edits === null)) {
    throw new StoreError("Invalid edits", 400, "invalid")
  }
  const edits = (record.edits ?? {}) as { qty?: unknown; price?: unknown }
  return {
    decision: "edit",
    edits: {
      qty: readPositive(edits.qty, "Quantity"),
      price: readPositive(edits.price, "Price"),
    },
  }
}

export async function POST(
  request: Request,
  context: { params: Promise<{ cardId: string }> },
) {
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return invalid("Invalid JSON")
  }

  try {
    const { cardId } = await context.params
    return Response.json(decideCard(cardId, parseDecision(body)))
  } catch (error) {
    return asErrorResponse(error)
  }
}
