"use client"

import type { ReactNode } from "react"
import { AdvisoryNote } from "@/features/cards/advisory-note"
import { useExpired } from "@/features/cards/countdown"
import { QTY_ACTIONS, SIZED_ACTIONS } from "@/features/cards/card-actions"
import { CardDetails } from "@/features/cards/card-details"
import { TradeIdentity } from "@/features/cards/option-details"
import { PreflightSummary } from "@/features/cards/preflight-summary"
import { RiskSummary } from "@/features/cards/risk-summary"
import { SourceMessages } from "@/features/cards/source-messages"
import { TradeSummary } from "@/features/cards/trade-summary"
import { TradeTargets } from "@/features/cards/trade-targets"
import { isDecidable } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { Card as SummaryCard, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

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
  const author = card.providerId ? `Provider ${card.providerId}` : "Manual card"
  const decidable = isDecidable(card)
  const qtyEditable = decidable && QTY_ACTIONS.has(card.action)
  const legsEditable = decidable && SIZED_ACTIONS.has(card.action) && card.proposal.tpLegs.length > 0

  return (
    <SummaryCard
      role="article"
      tabIndex={0}
      aria-label={`${author}, ${card.action} ${card.underlying ?? ""}`.trim()}
      data-card-id={card.id}
      data-expired={expired}
      className={cn("gap-0 py-0 outline-none focus-visible:ring-3 focus-visible:ring-ring/50", expired && "opacity-60 grayscale")}
    >
      <CardContent className="flex flex-col gap-4">
        <TradeSummary card={card} order={order} linked={linked} qtyEditable={qtyEditable} />
        <TradeIdentity card={card} />
        <TradeTargets card={card} showLegs={!legsEditable} />
        <RiskSummary card={card} />
        {decidable ? <PreflightSummary cardId={card.id} /> : null}
        <AdvisoryNote note={card.note} hidden={card.noteHidden} />
        <SourceMessages card={card} />
        {actions}
        <CardDetails card={card} order={order} />
      </CardContent>
    </SummaryCard>
  )
}
