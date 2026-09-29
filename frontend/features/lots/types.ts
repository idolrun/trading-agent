import type { ListFilters, Schemas } from "@/lib/api/types"

export type Lot = Schemas["LotOut"]
export type LotDetail = Schemas["LotDetail"]
export type LotListFilters = ListFilters<"list_lots_api_v1_lots_get">
