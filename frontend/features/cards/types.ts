export const CARD_STATES = [
  "pending",
  "approved",
  "submitted",
  "rejected",
  "expired",
  "invalidated",
  "disabled",
] as const

export type CardState = (typeof CARD_STATES)[number]

export const CARD_TYPES = ["normal", "inferred", "review", "disabled"] as const

export type CardType = (typeof CARD_TYPES)[number]

export const CARD_ACTIONS = [
  "entry",
  "add",
  "exit",
  "trim",
  "stop",
  "tp",
  "close",
] as const

export type CardAction = (typeof CARD_ACTIONS)[number]

export type CardProposal = {
  qty: number | null
  price: number | null
  warnings: string[]
}

export type Card = {
  id: string
  messageId: string
  signalIndex: number
  action: CardAction
  cardType: CardType
  state: CardState
  providerId: string
  underlying: string
  proposal: CardProposal
  validUntil: string
  lotId: string | null
}

export type CardListFilters = {
  state?: CardState
  providerId?: string
}

export type CardDecision =
  | { decision: "approve" }
  | { decision: "reject" }
  | { decision: "edit"; edits: { qty?: number; price?: number } }

export function isCardState(value: string | null): value is CardState {
  return CARD_STATES.some((state) => state === value)
}
