import type { ListFilters, Schemas } from "@/lib/api/types"

export type Lot = Schemas["LotOut"]
export type LotDetail = Schemas["LotDetail"]
export type LotListFilters = ListFilters<"list_lots_api_v1_lots_get">

/** Our lot average is not the broker's cost basis (TRD 16.2 #10): always shown with this label. */
export const AVG_LABEL = "system average — may differ from broker cost basis"
