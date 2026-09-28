import type { QueryClient, QueryKey } from "@tanstack/react-query"

export type CacheSnapshot = Array<[QueryKey, unknown]>

export function snapshotQueries(
  queryClient: QueryClient,
  queryKey: QueryKey,
): CacheSnapshot {
  return queryClient.getQueriesData({ queryKey })
}

export function restoreQueries(
  queryClient: QueryClient,
  snapshot: CacheSnapshot | undefined,
) {
  if (!snapshot) return
  for (const [queryKey, data] of snapshot) {
    queryClient.setQueryData(queryKey, data)
  }
}
