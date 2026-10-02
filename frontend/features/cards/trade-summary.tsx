import Link from "next/link"
import { Badge } from "@/components/badge"
import { cardStateLabel, estimatedValue, money, orderStateLabel, side, TONE_CLASS } from "@/features/cards/card-state"
import { Countdown } from "@/features/cards/countdown"
import { Field, Inferred } from "@/features/cards/trade-field"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { cn } from "@/lib/utils"

/**
 * BUY/SELL + ticker + where it stands, then the numbers that size the trade. `qtyEditable`
 * leaves the quantity to the inline input in the actions, so it is not shown twice.
 */
export function TradeSummary({
  card,
  order,
  linked,
  qtyEditable,
}: {
  card: Card
  order?: Order | null
  linked: boolean
  qtyEditable: boolean
}) {
  const { proposal } = card
  const action = side(card.action)
  const ticker = card.underlying ?? card.contract?.underlying ?? null
  // Once the card has an order, the order's state is the one that moves.
  const state = order ? orderStateLabel(order) : cardStateLabel(card.state)
  const value = estimatedValue(card)

  const title = (
    <>
      <Badge className={cn("mr-2 align-middle text-sm font-semibold uppercase", TONE_CLASS[action.tone])}>
        {action.label}
      </Badge>
      {ticker == null ? (
        <span className="text-lg font-medium text-warning-foreground">Unknown underlying</span>
      ) : (
        <Inferred show={card.inferredFields.some((item) => item.field === "underlying")}>{ticker}</Inferred>
      )}
    </>
  )

  return (
    <header className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 text-2xl font-semibold break-words">
          {linked ? (
            <Link href={`/cards/${card.id}`} className="hover:underline">
              {title}
            </Link>
          ) : (
            title
          )}
        </h3>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {card.state === "disabled" ? null : (
            <Badge className={cn("bg-transparent", TONE_CLASS[state.tone])}>{state.label}</Badge>
          )}
          {card.state === "pending" ? <Countdown validUntil={card.validUntil} /> : null}
        </div>
      </div>
      <dl className={cn("grid gap-x-4 gap-y-3", qtyEditable ? "grid-cols-2" : "grid-cols-3")}>
        {qtyEditable ? null : (
          <Field label="Qty" large>
            {proposal.qty ?? "—"}
          </Field>
        )}
        <Field label="Price" large>
          {money(proposal.price)}
        </Field>
        <Field label="Est. value" large>
          {value == null ? "—" : `~${money(value)}`}
        </Field>
      </dl>
    </header>
  )
}
