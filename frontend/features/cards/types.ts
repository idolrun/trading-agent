import type { ListFilters, Schemas } from "@/lib/api/types"

export type Card = Schemas["CardOut"]
export type CardDetail = Schemas["CardDetail"]
export type CardState = Schemas["CardState"]
export type CardProposal = Schemas["ProposalOut"]
export type CardListFilters = ListFilters<"list_cards_api_v1_cards_get">
export type CardEdits = Schemas["EditsIn"]
export type BreakevenPick = NonNullable<CardEdits["breakevenChoice"]>

/** POST body; `version` is the card version the user saw (optimistic concurrency). */
export type CardDecision = Schemas["DecisionRequest"]

/** What the server offers actions for: a pending card that is not disabled (C75). */
export function isDecidable(card: Card): boolean {
  return card.state === "pending" && card.cardType !== "disabled" && card.proposal.disabledReason == null
}

/** A code from the server ("lot_too_small") as words, for codes the client has no copy for. */
export function words(code: string): string {
  const text = code.replaceAll("_", " ").replaceAll(":", ": ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}
