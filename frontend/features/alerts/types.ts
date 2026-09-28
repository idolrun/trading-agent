export const ALERT_SEVERITIES = ["info", "urgent"] as const

export type AlertSeverity = (typeof ALERT_SEVERITIES)[number]

export type Alert = {
  id: string
  severity: AlertSeverity
  message: string
  acknowledged: boolean
  cardId: string | null
  createdAt: string
}

export type AlertListFilters = {
  acknowledged?: boolean
  severity?: AlertSeverity
}

export function isAlertSeverity(value: string | null): value is AlertSeverity {
  return ALERT_SEVERITIES.some((severity) => severity === value)
}

export function alertMatchesFilters(alert: Alert, filters: AlertListFilters): boolean {
  if (filters.acknowledged !== undefined && alert.acknowledged !== filters.acknowledged) {
    return false
  }
  if (filters.severity && alert.severity !== filters.severity) return false
  return true
}
