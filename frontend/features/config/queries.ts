import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import { ApiError } from "@/lib/api/errors"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import { defaultDocument, missingKeys, type ConfigResponse } from "@/features/config/types"

export const configKeys = domainKeys("config")

/** 404 (`not_found`) = never initialised: start from the defaults at version 0. */
export async function fetchConfig(): Promise<ConfigResponse> {
  try {
    return await apiGet<ConfigResponse>("/api/v1/config")
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error
    const document = defaultDocument()
    return { version: 0, document, missingForEntries: missingKeys(document) }
  }
}

/**
 * The editor keeps its own copy of what it loaded (the version it saves against), so
 * nothing refetches behind it; `gcTime: 0` makes every visit load fresh.
 */
export const configQuery = queryOptions({
  queryKey: configKeys.detail("current"),
  queryFn: fetchConfig,
  staleTime: Infinity,
  gcTime: 0,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
})

export const useConfig = () => useQuery(configQuery)

export type ConfigChangePage = Schemas["Page_ConfigChangeOut_"]

const historyQuery = infiniteQueryOptions({
  queryKey: configKeys.list({}),
  queryFn: ({ pageParam }) => apiGet<ConfigChangePage>("/api/v1/config/changes", { cursor: pageParam, limit: 20 }),
  initialPageParam: undefined as string | undefined,
  getNextPageParam: (last) => last.nextCursor ?? undefined,
})

export function useConfigChanges() {
  return useInfiniteQuery({ ...historyQuery, select: (data) => ({ items: data.pages.flatMap((page) => page.items) }) })
}
