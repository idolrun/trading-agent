"use client"

import { QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { getQueryClient } from "@/lib/query/get-query-client"

/**
 * Server payloads live in this QueryClient cache.
 * Screens keep filters, drafts, and selection in component state and pass
 * those values into query keys. They do not copy responses into context.
 */
export function QueryProvider({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={getQueryClient()}>{children}</QueryClientProvider>
  )
}
