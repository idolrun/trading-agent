import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { ItemList } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { Alert, AlertListFilters } from "@/features/alerts/types"

export const alertKeys = domainKeys("alerts")

export type AlertList = ItemList<Alert>

export function fetchAlertList(filters: AlertListFilters): Promise<AlertList> {
  return apiGet<AlertList>("/api/v1/alerts", filters)
}

export function alertListQuery(filters: AlertListFilters) {
  return queryOptions({
    queryKey: alertKeys.list(filters),
    queryFn: () => fetchAlertList(filters),
  })
}

export function useAlertList(filters: AlertListFilters) {
  return useQuery(alertListQuery(filters))
}
