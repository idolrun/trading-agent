"use client"

import type { ReactNode } from "react"
import type { UseQueryResult } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"

type QueryStateProps<TData> = {
  query: UseQueryResult<TData, Error>
  isEmpty?: (data: TData) => boolean
  loadingLabel: string
  emptyTitle: string
  emptyDescription?: string
  children: (data: TData) => ReactNode
}

type Freshness = "none" | "fresh" | "stale" | "refetching" | "placeholder" | "refetch-error"

function freshnessOf<TData>(query: UseQueryResult<TData, Error>): Freshness {
  if (query.data === undefined) return "none"
  if (query.isPlaceholderData) return "placeholder"
  if (query.isRefetching) return "refetching"
  if (query.isRefetchError) return "refetch-error"
  if (query.isPaused || query.isStale) return "stale"
  return "fresh"
}

const freshnessLabel: Record<Exclude<Freshness, "none">, string> = {
  fresh: "Up to date",
  stale: "Stale",
  refetching: "Updating",
  placeholder: "Showing the previous result",
  "refetch-error": "Refresh failed. Showing the last result.",
}

export function QueryState<TData>({
  query,
  isEmpty,
  loadingLabel,
  emptyTitle,
  emptyDescription,
  children,
}: QueryStateProps<TData>) {
  const data = query.data
  const retry = () => {
    void query.refetch()
  }

  if (data === undefined) {
    if (query.isError) {
      return (
        <div data-query-phase="error" data-freshness="none" role="alert" className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <div>
            <p className="font-medium">Couldn&apos;t load this</p>
            <p className="text-sm text-muted-foreground">{query.error.message}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Retry
          </Button>
        </div>
      )
    }

    return (
      <div data-query-phase="loading" data-freshness="none" aria-busy="true" className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">{loadingLabel}</p>
        <div className="h-16 animate-pulse rounded-lg bg-muted" />
        <div className="h-16 animate-pulse rounded-lg bg-muted" />
      </div>
    )
  }

  const empty = !query.isPlaceholderData && (isEmpty?.(data) ?? false)
  const freshness = freshnessOf(query)

  return (
    <div
      data-query-phase={empty ? "empty" : "ready"}
      data-freshness={freshness}
      className="flex flex-col gap-3"
    >
      <p className="min-h-5 text-xs text-muted-foreground" role="status" aria-live="polite">
        {freshness === "none" ? "" : freshnessLabel[freshness]}
      </p>
      {query.isRefetchError ? (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
          <p className="text-sm text-muted-foreground">{query.error.message}</p>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Retry
          </Button>
        </div>
      ) : null}
      {empty ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-8">
          <p className="font-medium">{emptyTitle}</p>
          {emptyDescription ? (
            <p className="mt-1 text-sm text-muted-foreground">{emptyDescription}</p>
          ) : null}
        </div>
      ) : (
        children(data)
      )}
    </div>
  )
}
