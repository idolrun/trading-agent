import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import { domainKeys } from "@/lib/query/keys"
import type { LabelProgress, QueueFilters, QueuePage } from "@/features/labels/types"

export const labelKeys = { ...domainKeys("labels"), progress: ["labels", "progress"] as const }

/** No refetchInterval and no focus refetch: the queue must not shift under the labeler. */
export function queueQuery(filters: QueueFilters) {
  return infiniteQueryOptions({
    queryKey: labelKeys.list(filters),
    queryFn: ({ pageParam }) =>
      apiGet<QueuePage>("/api/v1/labels/queue", {
        providerId: filters.providerId as unknown as number | undefined,
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    refetchOnWindowFocus: false,
  })
}

export function useLabelQueue(filters: QueueFilters) {
  return useInfiniteQuery({
    ...queueQuery(filters),
    select: (data) => ({ items: data.pages.flatMap((page) => page.items) }),
  })
}

export function useLabelProgress() {
  return useQuery(
    queryOptions({
      queryKey: labelKeys.progress,
      queryFn: () => apiGet<LabelProgress>("/api/v1/labels/progress"),
    }),
  )
}
