"use client"

import { ChevronDown } from "lucide-react"
import { orderStateLabel, money } from "@/features/cards/card-state"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Field } from "@/features/cards/trade-field"
import { words } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { formatTime } from "@/lib/format"

/**
 * Everything that is true of the card but does not decide it: who posted it, when, the broker
 * order, the server's review reasons. Collapsed by default.
 */
export function CardDetails({ card, order }: { card: Card; order?: Order | null }) {
  const { proposal } = card

  return (
    <Collapsible className="group/details">
      <CollapsibleTrigger className="inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        Details
        <ChevronDown aria-hidden className="size-3.5 transition-transform group-data-[state=open]/details:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          <Field label="Provider">{card.providerId ?? "Manual"}</Field>
          <Field label="Posted">
            <time dateTime={card.postedAt}>{formatTime(card.postedAt)}</time>
          </Field>
          {proposal.signalPrice ? <Field label="Signal price">{money(proposal.signalPrice)}</Field> : null}
          {order ? (
            <>
              <Field label="Order status">{orderStateLabel(order).label}</Field>
              <Field label="Broker mode">
                <span className="capitalize">{order.mode}</span>
              </Field>
              <Field label="Order">
                <span className="capitalize">{order.kind}</span>
                {order.limitPrice ? ` limit ${money(order.limitPrice)}` : ""}
                {order.stopPrice ? ` stop ${money(order.stopPrice)}` : ""}
              </Field>
              <Field label="Filled">
                {order.filledQty} of {order.qty}
              </Field>
              <Field label="Contract" className="col-span-2 sm:col-span-3">
                <span className="font-mono">{order.brokerSymbol}</span>
              </Field>
            </>
          ) : null}
          {proposal.reviewReasons.length > 0 ? (
            <Field label="Review reasons" className="col-span-2 sm:col-span-3">
              {proposal.reviewReasons.map(words).join("; ")}
            </Field>
          ) : null}
        </dl>
      </CollapsibleContent>
    </Collapsible>
  )
}
