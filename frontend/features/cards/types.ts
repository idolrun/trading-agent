import type { ListFilters, Schemas } from "@/lib/api/types"

export type Card = Schemas["CardOut"]
export type CardDetail = Schemas["CardDetail"]
export type CardState = Schemas["CardState"]
export type CardProposal = Schemas["ProposalOut"]
export type CardListFilters = ListFilters<"list_cards_api_v1_cards_get">

/** POST body; `version` is the card version the user saw (optimistic concurrency). */
export type CardDecision = Schemas["DecisionRequest"]
