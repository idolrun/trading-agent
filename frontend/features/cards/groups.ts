import type { Card } from "@/features/cards/types"

/**
 * Where a card sits in the inbox. This only sorts what the server already says; it adds no state.
 *  - decide:  pending, nothing missing: a person can approve or reject it.
 *  - review:  pending, but the server needs a person to finish or confirm fields first.
 *  - blocked: cannot execute: the server disabled it, or its validity window has ended.
 *  - done:    approved, submitted, rejected, expired, invalidated: history.
 */
export type CardGroup = "decide" | "review" | "blocked" | "done"

export function cardGroup(card: Card, expired: boolean): CardGroup {
  const { proposal } = card
  if (card.state === "disabled" || card.cardType === "disabled" || proposal.disabledReason != null) {
    return card.state === "pending" || card.state === "disabled" ? "blocked" : "done"
  }
  if (card.state !== "pending") return "done"
  if (expired) return "blocked"
  if (proposal.missingFields.length > 0 || card.cardType === "review") return "review"
  return "decide"
}

/** Display only: the server decides expiry. A card with no deadline never expires here. */
export const isPastDeadline = (card: Card, now: number): boolean =>
  card.validUntil != null && Date.parse(card.validUntil) <= now

/** Why a blocked card cannot be approved, in a sentence. */
export function blockedReason(card: Card, expired: boolean): string {
  const { disabledReason } = card.proposal
  if (disabledReason) return disabledReason
  if (card.state === "disabled" || card.cardType === "disabled") return "Disabled by the server."
  if (expired) return "Its validity window has ended, so approval will be refused."
  return "Cannot be approved right now."
}
