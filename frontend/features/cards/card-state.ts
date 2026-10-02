import { words } from "@/features/cards/types"
import { formatPrice } from "@/lib/format"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"

/** Semantic tones from the existing tokens (globals.css); there is no success/green token. */
export type Tone = "info" | "warning" | "destructive" | "neutral"

export const TONE_CLASS: Record<Tone, string> = {
  info: "border-info/40 bg-info-muted text-info-foreground",
  warning: "border-warning/40 bg-warning-muted text-warning-foreground",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
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
  intent: { label: "Queued", tone: "info" },
  submitted: { label: "Submitted", tone: "info" },
  working: { label: "Executing", tone: "info" },
  partially_filled: { label: "Partially filled", tone: "info" },
  filled: { label: "Filled", tone: "info" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  rejected: { label: "Failed", tone: "destructive" },
  unknown: { label: "Unknown", tone: "warning" },
}

export const cardStateLabel = (state: string): Label =>
  CARD_STATES[state] ?? { label: words(state), tone: "neutral" }

export const orderStateLabel = (order: Pick<Order, "status">): Label =>
  ORDER_STATES[order.status] ?? { label: words(order.status), tone: "neutral" }

/** BUY / SELL for the actions that open or reduce a position; any other action keeps its own name. */
export function side(action: string): { label: string; tone: Tone } {
  if (action === "entry" || action === "add") return { label: "Buy", tone: "info" }
  if (action === "trim" || action === "exit") return { label: "Sell", tone: "destructive" }
  return { label: words(action), tone: "neutral" }
}

/** Option premiums are per share; one contract covers 100. */
const OPTION_MULTIPLIER = 100

/** qty x price (x100 for options), or null while either is missing. Display only. */
export function estimatedValue(card: Card): number | null {
  const { qty, price } = card.proposal
  if (qty == null || price == null || Number.isNaN(Number(price))) return null
  return qty * Number(price) * (card.contract?.instrument === "option" ? OPTION_MULTIPLIER : 1)
}

/** A price with a dollar sign; "—" while it is missing. */
export const money = (value: string | number | null | undefined): string =>
  value == null ? "—" : `$${formatPrice(value)}`
