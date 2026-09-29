import type { ListFilters, Schemas } from "@/lib/api/types"

export type ProviderPosition = Schemas["PositionOut"]
export type PositionDetail = Schemas["PositionDetail"]
export type PositionListFilters = ListFilters<"list_positions_api_v1_positions_get">
export type PositionStatus = Schemas["PositionStatus"]
export const POSITION_STATUSES: PositionStatus[] = ["open", "trimmed", "closed"]
