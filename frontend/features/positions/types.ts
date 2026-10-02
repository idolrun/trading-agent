import type { ListFilters, Schemas } from "@/lib/api/types"

export type ProviderPosition = Schemas["PositionOut"]
export type PositionDetail = Schemas["PositionDetail"]
export type PositionListFilters = ListFilters<"list_positions_api_v1_positions_get">
export type PositionStatus = Schemas["PositionStatus"]
// watch / conditional: setups the provider named but has not entered (F072), never a holding
export const POSITION_STATUSES: PositionStatus[] = ["watch", "conditional", "open", "trimmed", "closed"]
