import type { Alert, AlertSeverity } from "@/features/alerts/types"

/**
 * Alerts that are the same occurrence repeating: same kind, severity, message and card. The
 * backend gives an alert no incident id, so this is the strictest key the data supports;
 * a different message or card never merges, and every event stays reachable in `events`.
 */
export type AlertGroup = {
  key: string
  severity: AlertSeverity
  kind: string
  message: string
  cardId: string | null
  /** Newest first. */
  events: Alert[]
  open: Alert[]
  firstAt: string
  lastAt: string
}

const RANK: Record<AlertSeverity, number> = { urgent: 0, warning: 1, info: 2 }

export function groupAlerts(alerts: Alert[]): AlertGroup[] {
  const groups = new Map<string, AlertGroup>()
  for (const alert of alerts) {
    const key = JSON.stringify([alert.kind, alert.severity, alert.message, alert.cardId])
    const group = groups.get(key) ?? {
      key,
      severity: alert.severity as AlertSeverity,
      kind: alert.kind,
      message: alert.message,
      cardId: alert.cardId,
      events: [],
      open: [],
      firstAt: alert.createdAt,
      lastAt: alert.createdAt,
    }
    group.events.push(alert)
    if (!alert.acknowledged) group.open.push(alert)
    if (alert.createdAt < group.firstAt) group.firstAt = alert.createdAt
    if (alert.createdAt > group.lastAt) group.lastAt = alert.createdAt
    groups.set(key, group)
  }
  const out = [...groups.values()]
  for (const group of out) group.events.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  // Unacknowledged first, then by severity, then newest.
  return out.sort(
    (a, b) =>
      Number(a.open.length === 0) - Number(b.open.length === 0) ||
      RANK[a.severity] - RANK[b.severity] ||
      b.lastAt.localeCompare(a.lastAt),
  )
}
