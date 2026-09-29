import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { LotDetail, LotListFilters } from "@/features/lots/types"

export const lotKeys = domainKeys("lots")

export type LotPage = Schemas["Page_LotOut_"]

export function fetchLotPage(filters: LotListFilters, cursor?: string): Promise<LotPage> {
  return apiGet<LotPage>("/api/v1/lots", { ...filters, cursor })
}

export function fetchLot(lotId: string): Promise<LotDetail> {
  return apiGet<LotDetail>(`/api/v1/lots/${encodeURIComponent(lotId)}`)
}

/** "Load more" follows `nextCursor`; a refetch (SSE, poll) reloads every loaded page. */
export function lotListQuery(filters: LotListFilters) {
  return infiniteQueryOptions({
    queryKey: lotKeys.list(filters),
    queryFn: ({ pageParam }) => fetchLotPage(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })
}

export function lotDetailQuery(lotId: string) {
  return queryOptions({
    queryKey: lotKeys.detail(lotId),
    queryFn: () => fetchLot(lotId),
    enabled: lotId.length > 0,
  })
}

export function useLotList(filters: LotListFilters) {
  return useInfiniteQuery({
    ...lotListQuery(filters),
    select: (data) => ({ items: data.pages.flatMap((page) => page.items) }),
  })
}

export function useLot(lotId: string) {
  return useQuery(lotDetailQuery(lotId))
}
