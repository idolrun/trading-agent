import { infiniteQueryOptions, useInfiniteQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import { domainKeys } from "@/lib/query/keys"
import { feedItemKey } from "@/features/feed/types"
import type { FeedFilters, FeedPage } from "@/features/feed/types"

export const feedKeys = domainKeys("feed")

export function fetchFeedPage(filters: FeedFilters, cursor?: string): Promise<FeedPage> {
  return apiGet<FeedPage>("/api/v1/feed", { ...filters, cursor })
}

/** A refetch (SSE `feed.message`, poll) reloads every loaded page. */
export function feedListQuery(filters: FeedFilters) {
  return infiniteQueryOptions({
    queryKey: feedKeys.list(filters),
    queryFn: ({ pageParam }) => fetchFeedPage(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    staleTime: 4_000,
    refetchInterval: 8_000,
  })
}

export function useFeed(filters: FeedFilters) {
  return useInfiniteQuery({
    ...feedListQuery(filters),
    select: (data) => {
      // A page boundary can repeat a row when new messages shift the cursor: keep the first.
      const seen = new Set<string>()
      const items = data.pages
        .flatMap((page) => page.items)
        .filter((item) => {
          const key = feedItemKey(item)
          return seen.has(key) ? false : (seen.add(key), true)
        })
      return { items, pageCount: data.pages.length }
    },
  })
}
