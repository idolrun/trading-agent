import { QueryClient } from "@tanstack/react-query"

import { queryClientDefaults } from "@/lib/query/defaults"

export function makeQueryClient() {
  return new QueryClient({ defaultOptions: queryClientDefaults })
}

let browserQueryClient: QueryClient | undefined

export function getQueryClient() {
  if (typeof window === "undefined") {
    return makeQueryClient()
  }

  browserQueryClient ??= makeQueryClient()
  return browserQueryClient
}
