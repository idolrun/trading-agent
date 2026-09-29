"use client"

import type { UseInfiniteQueryResult } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"

export function LoadMore({ query }: { query: Pick<UseInfiniteQueryResult, "hasNextPage" | "isFetchingNextPage" | "fetchNextPage"> }) {
  if (!query.hasNextPage) return null
  return (
    <Button
      type="button"
      variant="outline"
      className="h-11"
      disabled={query.isFetchingNextPage}
      onClick={() => void query.fetchNextPage()}
    >
      {query.isFetchingNextPage ? "Loading…" : "Load more"}
    </Button>
  )
}
