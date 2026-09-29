import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { PositionDetail, PositionListFilters } from "@/features/positions/types"

export const positionKeys = domainKeys("positions")

export type PositionPage = Schemas["Page_PositionOut_"]

export function fetchPositionPage(filters: PositionListFilters, cursor?: string): Promise<PositionPage> {
  return apiGet<PositionPage>("/api/v1/positions", { ...filters, cursor })
}

export function fetchPosition(positionId: string): Promise<PositionDetail> {
  return apiGet<PositionDetail>(
    `/api/v1/positions/${encodeURIComponent(positionId)}`,
  )
}

/** "Load more" follows `nextCursor`; a refetch (SSE, poll) reloads every loaded page. */
export function positionListQuery(filters: PositionListFilters) {
  return infiniteQueryOptions({
    queryKey: positionKeys.list(filters),
    queryFn: ({ pageParam }) => fetchPositionPage(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })
}

export function positionDetailQuery(positionId: string) {
  return queryOptions({
    queryKey: positionKeys.detail(positionId),
    queryFn: () => fetchPosition(positionId),
    enabled: positionId.length > 0,
  })
}

export function usePositionList(filters: PositionListFilters) {
  return useInfiniteQuery({
    ...positionListQuery(filters),
    select: (data) => ({ items: data.pages.flatMap((page) => page.items) }),
  })
}

export function usePosition(positionId: string) {
  return useQuery(positionDetailQuery(positionId))
}
