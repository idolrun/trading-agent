import type { NextRequest } from "next/server"
import { asErrorResponse, invalid } from "@/app/api/v1/_http"
import { listAlerts } from "@/app/api/v1/_store"
import { isAlertSeverity, type AlertListFilters } from "@/features/alerts/types"

function readBoolean(value: string): boolean | undefined {
  if (value === "true") return true
  if (value === "false") return false
  return undefined
}

export async function GET(request: NextRequest) {
  const filters: AlertListFilters = {}
  const acknowledged = request.nextUrl.searchParams.get("acknowledged")
  if (acknowledged !== null) {
    const parsed = readBoolean(acknowledged)
    if (parsed === undefined) return invalid("Invalid acknowledged filter")
    filters.acknowledged = parsed
  }
  const severity = request.nextUrl.searchParams.get("severity")
  if (severity) {
    if (!isAlertSeverity(severity)) return invalid("Unknown alert severity")
    filters.severity = severity
  }

  try {
    return Response.json({ items: listAlerts(filters) })
  } catch (error) {
    return asErrorResponse(error)
  }
}
