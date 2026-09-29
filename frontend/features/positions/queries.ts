import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { PositionDetail, PositionListFilters } from "@/features/positions/types"

export const positionKeys = domainKeys("positions")

export type PositionList = Schemas["Page_PositionOut_"]

export function fetchPositionList(filters: PositionListFilters): Promise<PositionList> {
  return apiGet<PositionList>("/api/v1/positions", filters)
}

export function fetchPosition(positionId: string): Promise<PositionDetail> {
  return apiGet<PositionDetail>(
    `/api/v1/positions/${encodeURIComponent(positionId)}`,
  )
}

export function positionListQuery(filters: PositionListFilters) {
  return queryOptions({
    queryKey: positionKeys.list(filters),
    queryFn: () => fetchPositionList(filters),
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
  return useQuery(positionListQuery(filters))
}

export function usePosition(positionId: string) {
  return useQuery(positionDetailQuery(positionId))
}
