import type { Card, CardDecision, CardListFilters } from "@/features/cards/types"

export function cardMatchesFilters(card: Card, filters: CardListFilters): boolean {
  if (filters.state && card.state !== filters.state) return false
  if (filters.providerId && card.providerId !== filters.providerId) return false
  return true
}

export function applyCardDecision(card: Card, decision: CardDecision): Card {
  if (decision.decision === "approve") {
    return { ...card, state: "approved" }
  }
  if (decision.decision === "reject") {
    return { ...card, state: "rejected" }
  }
  return {
    ...card,
    proposal: {
      ...card.proposal,
      qty: decision.edits.qty ?? card.proposal.qty,
      price: decision.edits.price ?? card.proposal.price,
      warnings:
        (decision.edits.qty ?? card.proposal.qty) == null
          ? card.proposal.warnings
          : card.proposal.warnings.filter((warning) => warning !== "Quantity is required"),
    },
  }
}
