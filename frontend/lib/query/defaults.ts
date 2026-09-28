import type { DefaultOptions } from "@tanstack/react-query"
import { retryServerErrors } from "@/lib/query/retry"

export const queryClientDefaults = {
  queries: {
    staleTime: 10_000,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    retry: retryServerErrors,
  },
  mutations: {
    retry: false,
  },
} satisfies DefaultOptions
