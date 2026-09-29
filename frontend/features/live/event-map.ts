import type { QueryClient, QueryKey } from "@tanstack/react-query"
import { alertKeys } from "@/features/alerts/queries"
import { cardKeys } from "@/features/cards/queries"
import { healthKeys } from "@/features/health/queries"
import { lotKeys } from "@/features/lots/queries"
import { orderKeys } from "@/features/orders/queries"
import { positionKeys } from "@/features/positions/queries"

/**
 * Backend event type (`app/notify/bus.py`, emitted by pipeline/apply, approval_service,
 * execution/fills, alerts, system/flags) -> query keys to refetch. `id` is the row id, except
 * `flag.changed` (flag name) and `fill.created` (a fill: there is no fill query).
 * `resync` = events may have been missed: everything is refetched.
 */
const invalidations: Record<string, (id: string) => readonly QueryKey[]> = {
  "card.created": () => [cardKeys.lists()],
  "card.updated": (id) => [cardKeys.lists(), cardKeys.detail(id)],
  "order.created": (id) => [orderKeys.lists(), orderKeys.detail(id)],
  "order.updated": (id) => [orderKeys.lists(), orderKeys.detail(id)],
  "fill.created": () => [orderKeys.lists(), lotKeys.lists(), positionKeys.lists()],
  "lot.updated": (id) => [lotKeys.lists(), lotKeys.detail(id), positionKeys.lists()],
  "position.updated": (id) => [positionKeys.lists(), positionKeys.detail(id)],
  "alert.created": () => [alertKeys.lists()],
  "flag.changed": () => [healthKeys.all],
}

export const RESYNC = "resync"
/** Every named SSE event the client subscribes to. */
export const EVENT_TYPES = [...Object.keys(invalidations), RESYNC]

/** Empty for an unknown type: a newer backend must not break an older client. */
export function queryKeysFor(type: string, id: string): readonly QueryKey[] {
  return invalidations[type]?.(id) ?? []
}

export function applyEvent(client: QueryClient, type: string, id: string) {
  if (type === RESYNC) return client.invalidateQueries()
  return Promise.all(queryKeysFor(type, id).map((queryKey) => client.invalidateQueries({ queryKey })))
}
