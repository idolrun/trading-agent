export const POSITION_STATUSES = ["open", "trimmed", "closed", "corrected"] as const

export type PositionStatus = (typeof POSITION_STATUSES)[number]

export type ProviderPosition = {
  id: string
  providerId: string
  instrument: "option" | "stock"
  underlying: string
  expiry: string | null
  strike: number | null
  optionRight: "call" | "put" | null
  status: PositionStatus
}

export type PositionListFilters = {
  status?: PositionStatus
  providerId?: string
}

export function isPositionStatus(value: string | null): value is PositionStatus {
  return POSITION_STATUSES.some((status) => status === value)
}
