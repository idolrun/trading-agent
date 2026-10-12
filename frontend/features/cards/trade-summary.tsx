import Link from "next/link"
import { Badge } from "@/components/badge"
import { cardStateLabel, estimatedValue, money, orderStateLabel, side, TONE_CLASS } from "@/features/cards/card-state"
import { Countdown } from "@/features/cards/countdown"
import { labelBadge } from "@/features/cards/labels"
import { TradeIdentity } from "@/features/cards/option-details"
import { Field, Inferred } from "@/features/cards/trade-field"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { formatStamp, providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * Top of a card: action, symbol, contract, where it stands, who sent it; then the numbers that
 * size the trade. `qtyEditable` leaves the quantity to the inline input in the actions, so it
 * is not shown twice. A missing number says what is missing rather than printing a dash.
 */
export function TradeSummary({
  card,
  order,
  linked,
  qtyEditable,
  blockedLabel = null,
}: {
  card: Card
  order?: Order | null
  linked: boolean
  qtyEditable: boolean
  /** "Stale" or "Disabled" when the card cannot be approved: shown as a badge beside the card. */
  blockedLabel?: string | null
}) {
  const { proposal } = card
  const action = side(card.action)
  const ticker = card.underlying ?? card.contract?.underlying ?? null
  // Once the card has an order, the order's state is the one that moves.
  const state = order ? orderStateLabel(order) : cardStateLabel(card.state)
  const value = estimatedValue(card)
  const source = card.providerId ? providerLabel(card.providerId, card.providerName) : "Manual card"

  const title = (
    <>
      <Badge className={cn("mr-2 align-middle text-xs font-bold tracking-wide uppercase", TONE_CLASS[action.tone])}>
        {action.label}
      </Badge>
      {ticker == null ? (
        <span className="text-base font-semibold text-warning-foreground">Unknown underlying</span>
      ) : (
        <Inferred show={card.inferredFields.some((item) => item.field === "underlying")}>{ticker}</Inferred>
      )}
    </>
  )

  return (
    <header className="flex flex-col gap-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-xl font-semibold tracking-tight break-words">
            {linked ? (
              <Link href={`/cards/${card.id}`} className="hover:underline">
                {title}
              </Link>
            ) : (
              title
            )}
          </h3>
          <TradeIdentity card={card} />
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {blockedLabel ? (
            <Badge className="border-warning bg-warning/15 text-xs font-semibold tracking-wide uppercase">{blockedLabel}</Badge>
          ) : null}
          {card.state === "disabled" ? null : (
            <Badge className={cn("bg-transparent text-xs", TONE_CLASS[state.tone])}>{state.label}</Badge>
          )}
          {card.state === "pending" ? <Countdown validUntil={card.validUntil} /> : null}
        </div>
      </div>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span title={card.providerId ? `Provider ID ${card.providerId}` : undefined}>{source}</span>
        <span aria-hidden>·</span>
        <time dateTime={card.postedAt}>Posted {formatStamp(card.postedAt)}</time>
        {proposal.labels.map((label) => (
          <Badge key={label} className="bg-transparent px-1.5 py-0 text-xs text-warning-foreground">
            {labelBadge(label)}
          </Badge>
        ))}
      </p>
      <dl className={cn("grid gap-x-4 gap-y-2", qtyEditable ? "grid-cols-1" : "grid-cols-3")}>
        {qtyEditable ? null : (
          <Field label="Qty">{proposal.qty ?? <span className="font-normal text-muted-foreground">Not stated</span>}</Field>
        )}
        <Field label="Price">
          {proposal.price == null ? <span className="font-normal text-muted-foreground">No price in signal</span> : money(proposal.price)}
        </Field>
        {qtyEditable ? null : (
          <Field label="Est. value">
            {value == null ? (
              <span className="font-normal text-muted-foreground">Needs qty and price</span>
            ) : (
              `~${money(value)}`
            )}
          </Field>
        )}
      </dl>
    </header>
  )
}
