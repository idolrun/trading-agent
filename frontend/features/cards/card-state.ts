import { words } from "@/features/cards/types"
import { formatPrice } from "@/lib/format"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"

/** Semantic tones from the existing tokens (globals.css); there is no success/green token. */
export type Tone = "info" | "warning" | "destructive" | "success" | "neutral"

export const TONE_CLASS: Record<Tone, string> = {
  info: "border-info/40 bg-info-muted text-info-foreground",
  warning: "border-warning/40 bg-warning-muted text-warning-foreground",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  success: "border-success/40 bg-success-muted text-success-foreground",
  neutral: "text-muted-foreground",
}

type Label = { label: string; tone: Tone }

/** Card states the API returns (CardState). Unknown codes still show, as words. */
const CARD_STATES: Record<string, Label> = {
  pending: { label: "Pending approval", tone: "warning" },
  approved: { label: "Approved", tone: "info" },
  submitted: { label: "Submitted", tone: "info" },
  rejected: { label: "Rejected", tone: "destructive" },
  expired: { label: "Expired", tone: "neutral" },
  invalidated: { label: "Invalidated", tone: "destructive" },
  disabled: { label: "Disabled", tone: "neutral" },
}

/** Order statuses the API returns (OrderStatus): where execution and fills show up. */
const ORDER_STATES: Record<string, Label> = {
  intent: { label: "Intent created", tone: "neutral" },
  submitted: { label: "Submitted", tone: "info" },
  working: { label: "Working", tone: "info" },
  partially_filled: { label: "Partially filled", tone: "info" },
  filled: { label: "Filled", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  rejected: { label: "Rejected", tone: "destructive" },
  unknown: { label: "Unknown: reconcile", tone: "warning" },
}

export const cardStateLabel = (state: string): Label =>
  CARD_STATES[state] ?? { label: words(state), tone: "neutral" }

export const orderStateLabel = (order: Pick<Order, "status">): Label =>
  ORDER_STATES[order.status] ?? { label: words(order.status), tone: "neutral" }

/** The action as a trader names it: BUY/ADD open or grow a position, TRIM/EXIT reduce it. */
export function side(action: string): { label: string; tone: Tone } {
  switch (action) {
    case "entry":
      return { label: "Buy", tone: "info" }
    case "add":
      return { label: "Add", tone: "info" }
    case "trim":
      return { label: "Trim", tone: "neutral" }
    case "exit":
      return { label: "Exit", tone: "neutral" }
    case "close":
      return { label: "Close", tone: "neutral" }
    case "review":
      return { label: "Review", tone: "warning" }
    default:
      return { label: words(action), tone: "neutral" }
  }
}

/** Option premiums are per share; one contract covers 100. */
const OPTION_MULTIPLIER = 100

/** The unit the user is counting: shares for a stock, contracts for an option. */
export const qtyUnit = (card: Card): "shares" | "contracts" => (card.contract?.instrument === "stock" ? "shares" : "contracts")

/**
 * qty x price (x100 for options), or null while either is missing. Display only: it follows the
 * quantity the user typed and never decides one (C105).
 */
export function orderValue(card: Card, qty: number | null): number | null {
  const { price } = card.proposal
  if (qty == null || price == null || Number.isNaN(Number(price))) return null
  return qty * Number(price) * (card.contract?.instrument === "option" ? OPTION_MULTIPLIER : 1)
}

/** The value of the card's own quantity (a close, or a card that already has an order). */
export const estimatedValue = (card: Card): number | null => orderValue(card, card.proposal.qty)

/** A price with a dollar sign; "—" while it is missing. */
export const money = (value: string | number | null | undefined): string =>
  value == null ? "—" : `$${formatPrice(value)}`
