import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { HealthDetails, HealthWindow } from "@/features/health/types"

/** Every key sits under `["health"]`, so `flag.changed` -> `healthKeys.all` refreshes them all. */
export const healthKeys = {
  all: ["health"] as const,
  details: (window: HealthWindow) => ["health", "details", window] as const,
}

export function fetchHealthDetails(window: HealthWindow): Promise<HealthDetails> {
  return apiGet<HealthDetails>("/api/v1/health/details", { window })
}

export function healthDetailsQuery(window: HealthWindow) {
  return queryOptions({
    queryKey: healthKeys.details(window),
    queryFn: () => fetchHealthDetails(window),
    staleTime: 5_000,
    refetchInterval: 15_000,
  })
}

export function useHealthDetails(window: HealthWindow) {
  return useQuery(healthDetailsQuery(window))
}
