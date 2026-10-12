"use client"

import type { ReactNode } from "react"
import { AdvisoryNote } from "@/features/cards/advisory-note"
import { QTY_ACTIONS, SIZED_ACTIONS } from "@/features/cards/card-actions"
import { CardDetails } from "@/features/cards/card-details"
import { useExpired } from "@/features/cards/countdown"
import { blockedReason, cardGroup } from "@/features/cards/groups"
import { PreflightVerdict } from "@/features/cards/preflight-summary"
import { RiskSummary } from "@/features/cards/risk-summary"
import { TradeSummary } from "@/features/cards/trade-summary"
import { TradeTargets } from "@/features/cards/trade-targets"
import { isDecidable } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { Card as SummaryCard, CardContent } from "@/components/ui/card"
import { providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

/** What the original message said, in at most two lines. The full text is in Details. */
function signalText(card: Card): string | null {
  const message = card.sourceMessages.find((item) => item.messageId === card.messageId) ?? card.sourceMessages.at(-1)
  return message?.text?.trim() || null
}

export function CardSummary({
  card,
  linked = true,
  order,
  actions,
}: {
  card: Card
  linked?: boolean
  /** The broker order for this card, once it has one. */
  order?: Order | null
  actions?: ReactNode
}) {
  const expired = useExpired(card.validUntil)
  const author = card.providerId ? providerLabel(card.providerId, card.providerName) : "Manual card"
  const decidable = isDecidable(card)
  const group = cardGroup(card, expired)
  const qtyEditable = decidable && !expired && QTY_ACTIONS.has(card.action)
  const legsEditable = decidable && !expired && SIZED_ACTIONS.has(card.action) && card.proposal.tpLevels.length > 0
  const signal = signalText(card)
  const disabled = card.state === "disabled" || card.cardType === "disabled" || card.proposal.disabledReason != null

  return (
    <SummaryCard
      role="article"
      tabIndex={0}
      aria-label={`${author}, ${card.action} ${card.underlying ?? ""}`.trim()}
      data-card-id={card.id}
      data-provider-id={card.providerId ?? ""}
      data-action={card.action}
      data-underlying={card.underlying ?? ""}
      data-group={group}
      data-expired={expired}
      className={cn(
        "gap-0 py-0 outline-none focus-visible:ring-3 focus-visible:ring-ring/60",
        group === "blocked" && "bg-muted/30",
      )}
    >
      <CardContent className="flex flex-col gap-3 px-4 py-3">
        <TradeSummary
          card={card}
          order={order}
          linked={linked}
          qtyEditable={qtyEditable}
          blockedLabel={group === "blocked" ? (disabled ? "Disabled" : "Stale") : null}
        />
        {signal ? (
          <blockquote className="line-clamp-2 border-l-2 border-border pl-3 text-sm break-words whitespace-pre-wrap text-muted-foreground">
            {signal}
          </blockquote>
        ) : null}
        <TradeTargets card={card} showLegs={!legsEditable} />
        {group === "blocked" ? (
          <p role="note" className="text-sm">
            <span className="text-muted-foreground">Cannot be approved. </span>
            <span className="text-muted-foreground">{blockedReason(card, expired)}</span>
          </p>
        ) : (
          <RiskSummary card={card} />
        )}
        {decidable && !expired ? <PreflightVerdict cardId={card.id} /> : null}
        <AdvisoryNote note={card.note} hidden={card.noteHidden} />
        {actions}
        <CardDetails card={card} order={order} />
      </CardContent>
    </SummaryCard>
  )
}
