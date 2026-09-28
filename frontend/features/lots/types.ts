export const LOT_STATUSES = ["open", "closed"] as const

export type LotStatus = (typeof LOT_STATUSES)[number]

export type Lot = {
  id: string
  providerPositionId: string | null
  contract: string
  qty: number
  avgPrice: number
  isForeign: boolean
  status: LotStatus
}

export type LotListFilters = {
  status?: LotStatus
}

export function isLotStatus(value: string | null): value is LotStatus {
  return LOT_STATUSES.some((status) => status === value)
}
