"use client"

import { ChevronDown } from "lucide-react"
import Link from "next/link"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { orderStateLabel, money } from "@/features/cards/card-state"
import { PreflightSummary } from "@/features/cards/preflight-summary"
import { SourceMessages } from "@/features/cards/source-messages"
import { Field } from "@/features/cards/trade-field"
import { isDecidable, words } from "@/features/cards/types"
import type { Card, CardDetail } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { formatStamp, formatTime } from "@/lib/format"

/**
 * Everything that is true of the card but does not decide it: source messages and where each
 * inferred field came from, the full live checks, who posted it, the broker order, the server's
 * review reasons. Collapsed, except when a field was inferred from an earlier message: that
 * provenance should be in view before an approval.
 */
export function CardDetails({
  card,
  order,
  history,
  approval,
}: {
  card: Card
  order?: Order | null
  /** Present on the card detail route. The inbox list does not carry this history. */
  history?: CardDetail["events"]
  approval?: CardDetail["approval"]
}) {
  const { proposal } = card
  const literal = card.sourceMessages.some((message) => message.spans.length > 0)
  const contextual = card.inferredFields.length > 0

  return (
    <Collapsible defaultOpen={card.inferredFields.length > 0} className="group/details border-t border-border pt-1">
      <CollapsibleTrigger className="inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        Details
        <ChevronDown aria-hidden className="size-3.5 transition-transform group-data-[state=open]/details:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-3 pt-1">
        <SourceMessages card={card} />
        {literal || contextual ? (
          <p className="text-xs text-muted-foreground">
            {literal ? "Highlighted words were literal in the message. " : ""}
            {contextual ? "A marked field name came from an earlier message. " : ""}
            The API does not separately flag code-resolved fields.
          </p>
        ) : null}
        {isDecidable(card) ? <PreflightSummary cardId={card.id} /> : null}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          <Field label="Provider">
            {card.providerName ? `${card.providerName} (${card.providerId})` : (card.providerId ?? "Manual")}
          </Field>
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
          {card.providerPositionId ? (
            <Field label="Provider position" className="col-span-2 sm:col-span-3">
              <Link href={`/positions/${card.providerPositionId}`} className="underline underline-offset-2">
                View position
              </Link>
            </Field>
          ) : null}
          {approval ? (
            <Field label="Decision" className="col-span-2 sm:col-span-3">
              {words(approval.decision)} · <time dateTime={approval.decidedAt}>{formatStamp(approval.decidedAt)}</time>
              {approval.stepUp ? " · password confirmed" : ""}
            </Field>
          ) : null}
        </dl>
        {history ? (
          <section aria-label="Lifecycle" className="flex flex-col gap-1">
            <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Lifecycle</h4>
            {history.length === 0 ? (
              <p className="text-sm text-muted-foreground">No lifecycle events on this card.</p>
            ) : (
              <ol className="flex flex-col gap-1 text-sm">
                {history.map((event, index) => (
                  <li key={`${event.at}-${index}`} className="text-muted-foreground">
                    <time dateTime={event.at} className="tabular-nums text-foreground">
                      {formatStamp(event.at)}
                    </time>
                    {" · "}
                    {event.fromState ? `${words(event.fromState)} → ` : ""}
                    {words(event.toState)}
                    {" · "}
                    {words(event.reasonCode)}
                    {" · "}
                    {event.actor}
                  </li>
                ))}
              </ol>
            )}
          </section>
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  )
}
