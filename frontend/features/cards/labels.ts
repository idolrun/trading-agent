import { words } from "@/features/cards/types"

/**
 * Wording for the label codes the server puts on a proposal (`proposal.labels`). `hint` is the
 * sentence shown next to the quantity input while it is blank (7.3, C52, C82). An unknown code
 * still shows, as words.
 */
const COPY: Record<string, { badge: string; hint?: string }> = {
  late: { badge: "Late" },
  stale: { badge: "Stale" },
  suggestion: { badge: "Suggestion", hint: "Suggestion, provider still holding." },
  lot_too_small: { badge: "Lot too small", hint: "Lot too small to trim: exit all or skip." },
  candle_close: { badge: "Candle close" },
}

export const labelBadge = (code: string) => COPY[code]?.badge ?? words(code)
export const labelHint = (code: string) => COPY[code]?.hint
