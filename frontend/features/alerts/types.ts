import type { ListFilters, Schemas } from "@/lib/api/types"

export type Alert = Schemas["AlertOut"]
export type AlertSeverity = Schemas["Severity"]
export type AlertListFilters = ListFilters<"list_alerts_api_v1_alerts_get">

export function alertMatchesFilters(alert: Alert, filters: AlertListFilters): boolean {
  if (filters.acknowledged != null && alert.acknowledged !== filters.acknowledged) {
    return false
  }
  if (filters.severity && alert.severity !== filters.severity) return false
  return true
}
