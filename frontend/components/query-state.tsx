"use client"

import type { ReactNode } from "react"
import type { UseQueryResult } from "@tanstack/react-query"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { userFacingMessage } from "@/lib/api/errors"
import { Button } from "@/components/ui/button"
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"

type QueryStateProps<TData> = {
  query: UseQueryResult<TData, Error>
  isEmpty?: (data: TData) => boolean
  loadingLabel: string
  emptyTitle: string
  emptyDescription?: string
  /** Side panels: one inline line for empty, no "Up to date" row, a single loading bar. */
  compact?: boolean
  /** The caller renders the freshness line itself (see `freshnessOf` / `freshnessLabel`). */
  hideFreshness?: boolean
  /** `line` is a single sentence. The boxed empty panel is for places that need a destination. */
  emptyLayout?: "panel" | "line"
  children: (data: TData) => ReactNode
}

export type Freshness = "none" | "fresh" | "stale" | "refetching" | "placeholder" | "refetch-error"

export function freshnessOf<TData>(query: UseQueryResult<TData, Error>): Freshness {
  if (query.data === undefined) return "none"
  if (query.isPlaceholderData) return "placeholder"
  if (query.isRefetching) return "refetching"
  if (query.isRefetchError) return "refetch-error"
  if (query.isPaused || query.isStale) return "stale"
  return "fresh"
}

export const freshnessLabel: Record<Exclude<Freshness, "none">, string> = {
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
  compact = false,
  hideFreshness = false,
  emptyLayout = "panel",
  children,
}: QueryStateProps<TData>) {
  const data = query.data
  const retry = () => {
    void query.refetch()
  }

  if (data === undefined) {
    if (query.isError) {
      return (
        <Alert data-query-phase="error" data-freshness="none" variant="destructive" className="flex flex-col items-start gap-3">
          <AlertTitle>Couldn&apos;t load this</AlertTitle>
          <AlertDescription>{userFacingMessage(query.error)}</AlertDescription>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Retry
          </Button>
        </Alert>
      )
    }

    return (
      <div data-query-phase="loading" data-freshness="none" aria-busy="true" className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">{loadingLabel}</p>
        <Skeleton className="h-10 rounded-lg" />
        {compact ? null : <Skeleton className="h-16 rounded-lg" />}
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
      {hideFreshness || (compact && freshness === "fresh") ? null : (
        <p className="min-h-5 text-xs text-muted-foreground" role="status" aria-live="polite">
          {freshness === "none" ? "" : freshnessLabel[freshness]}
        </p>
      )}
      {query.isRefetchError ? (
        <Alert className="flex flex-row items-center justify-between gap-3">
          <AlertDescription>{userFacingMessage(query.error)}</AlertDescription>
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Retry
          </Button>
        </Alert>
      ) : null}
      {empty && (compact || emptyLayout === "line") ? (
        <p className="text-sm text-muted-foreground">
          <span>{emptyTitle}</span>
          {emptyDescription ? <span>. {emptyDescription}</span> : null}
        </p>
      ) : empty ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>{emptyTitle}</EmptyTitle>
            {emptyDescription ? <EmptyDescription>{emptyDescription}</EmptyDescription> : null}
          </EmptyHeader>
        </Empty>
      ) : (
        children(data)
      )}
    </div>
  )
}
