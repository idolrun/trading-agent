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
  candle_close: { badge: "Candle close" },
}

/** `provider_fraction:half`: what the provider said about the size. Information; you enter the quantity. */
const FRACTION = "provider_fraction:"
const fraction = (code: string) => (code.startsWith(FRACTION) ? code.slice(FRACTION.length) : null)

export const labelBadge = (code: string) => {
  const said = fraction(code)
  return said == null ? (COPY[code]?.badge ?? words(code)) : `Provider said ${said}`
}
export const labelHint = (code: string) => {
  const said = fraction(code)
  return said == null ? COPY[code]?.hint : `Provider said ${said}. You choose the quantity.`
}
