import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { FeedHealth } from "@/features/health/types"

export const healthKeys = {
  all: ["health"] as const,
  feed: () => ["health", "feed"] as const,
}

export function fetchFeedHealth(): Promise<FeedHealth> {
  return apiGet<FeedHealth>("/api/v1/health")
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
