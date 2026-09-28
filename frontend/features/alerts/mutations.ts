import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query"
import { apiPost } from "@/lib/api/client"
import { ApiError } from "@/lib/api/errors"
import { restoreQueries, snapshotQueries, type CacheSnapshot } from "@/lib/query/snapshot"
import { alertKeys, type AlertList } from "@/features/alerts/queries"
import { alertMatchesFilters, type Alert, type AlertListFilters } from "@/features/alerts/types"

function acknowledgeAlert(alertId: string): Promise<Alert> {
  return apiPost<Alert>(`/api/v1/alerts/${encodeURIComponent(alertId)}/acknowledge`)
}

function filtersFromListKey(queryKey: QueryKey): AlertListFilters {
  const filters = queryKey[2]
  if (!filters || typeof filters !== "object") return {}
  return filters as AlertListFilters
}

function patchAlertList(list: AlertList, next: Alert, filters: AlertListFilters): AlertList {
  const index = list.items.findIndex((alert) => alert.id === next.id)
  const matches = alertMatchesFilters(next, filters)
  if (index === -1) return matches ? { items: [next, ...list.items] } : list
  if (!matches) return { items: list.items.filter((alert) => alert.id !== next.id) }
  const items = list.items.slice()
  items[index] = next
  return { items }
}

export function useAcknowledgeAlert() {
  const queryClient = useQueryClient()

  return useMutation<Alert, ApiError, string, { snapshot: CacheSnapshot }>({
    mutationKey: [...alertKeys.all, "acknowledge"],
    mutationFn: acknowledgeAlert,
    onMutate: async (alertId) => {
      await queryClient.cancelQueries({ queryKey: alertKeys.all })
      const snapshot = snapshotQueries(queryClient, alertKeys.all)
      for (const query of queryClient.getQueryCache().findAll({ queryKey: alertKeys.lists() })) {
        queryClient.setQueryData<AlertList>(query.queryKey, (current) => {
          if (!current) return current
          const currentAlert = current.items.find((alert) => alert.id === alertId)
          if (!currentAlert) return current
          return patchAlertList(
            current,
            { ...currentAlert, acknowledged: true },
            filtersFromListKey(query.queryKey),
          )
        })
      }
      return { snapshot }
    },
    onError: (_error, _alertId, context) => {
      restoreQueries(queryClient, context?.snapshot)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: alertKeys.lists() }),
  })
}
