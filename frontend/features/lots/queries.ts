import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { ItemList } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { Lot, LotListFilters } from "@/features/lots/types"

export const lotKeys = domainKeys("lots")

export type LotList = ItemList<Lot>

export function fetchLotList(filters: LotListFilters): Promise<LotList> {
  return apiGet<LotList>("/api/v1/lots", filters)
}

export function fetchLot(lotId: string): Promise<Lot> {
  return apiGet<Lot>(`/api/v1/lots/${encodeURIComponent(lotId)}`)
}

export function lotListQuery(filters: LotListFilters) {
  return queryOptions({
    queryKey: lotKeys.list(filters),
    queryFn: () => fetchLotList(filters),
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
  return useQuery(lotListQuery(filters))
}

export function useLot(lotId: string) {
  return useQuery(lotDetailQuery(lotId))
}
