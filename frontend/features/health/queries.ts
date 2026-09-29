import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { HealthDetails } from "@/features/health/types"

export const healthKeys = {
  all: ["health"] as const,
  feed: () => ["health", "feed"] as const,
}

export function fetchFeedHealth(): Promise<HealthDetails> {
  return apiGet<HealthDetails>("/api/v1/health/details")
}

export function feedHealthQuery() {
  return queryOptions({
    queryKey: healthKeys.feed(),
    queryFn: fetchFeedHealth,
    staleTime: 5_000,
    refetchInterval: 15_000,
  })
}

export function useFeedHealth() {
  return useQuery(feedHealthQuery())
}
