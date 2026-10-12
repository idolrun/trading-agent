"use client"

import type { ReactNode } from "react"
import { Badge } from "@/components/badge"
import { AdvisoryNote } from "@/features/cards/advisory-note"
import { QTY_ACTIONS, SIZED_ACTIONS } from "@/features/cards/card-actions"
import { cardStateLabel, estimatedValue, money, orderStateLabel, side, TONE_CLASS } from "@/features/cards/card-state"
import { CardDetails } from "@/features/cards/card-details"
import { Countdown, useExpired } from "@/features/cards/countdown"
import { blockedReason, cardGroup } from "@/features/cards/groups"
import { labelBadge } from "@/features/cards/labels"
import { TradeIdentity } from "@/features/cards/option-details"
import { PreflightVerdict } from "@/features/cards/preflight-summary"
import { RiskSummary } from "@/features/cards/risk-summary"
import { Field, Inferred } from "@/features/cards/trade-field"
import { TradeTargets } from "@/features/cards/trade-targets"
import { isDecidable, words } from "@/features/cards/types"
import type { CardDetail } from "@/features/cards/types"
import type { Order } from "@/features/orders/types"
import { formatStamp, providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

function Missing({ children }: { children: string }) {
  return <span className="font-normal text-muted-foreground">{children}</span>
}

function formatExpiry(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number)
  if (!year || !month || !day) return iso
  return new Date(year, month - 1, day).toLocaleDateString("en-US", { dateStyle: "medium" })
}

const formatStrike = (strike: string) => `$${Number(strike).toLocaleString("en-US", { maximumFractionDigits: 4 })}`

function signalText(card: CardDetail): string | null {
  const message = card.sourceMessages.find((item) => item.messageId === card.messageId) ?? card.sourceMessages.at(-1)
  return message?.text ?? null
}

/**
 * The card route: one decision, in reading order. The inbox keeps the denser `CardSummary`.
 * Confirmed values and suggestions are separate fields. Nothing here is invented.
 */
export function CardReview({
  card,
  order,
  actions,
}: {
  card: CardDetail
  order?: Order | null
  actions?: ReactNode
}) {
  const expired = useExpired(card.validUntil)
  const { proposal } = card
  const action = side(card.action)
  const confirmed = card.underlying
  const suggested = !confirmed ? proposal.suggestedUnderlying : null
  const option = card.contract?.instrument === "option"
  const stock = card.contract?.instrument === "stock"
  const inferred = new Set(card.inferredFields.map((item) => item.field))
  const decidable = isDecidable(card)
  const group = cardGroup(card, expired)
  const qtyEditable = decidable && !expired && QTY_ACTIONS.has(card.action)
  const legsEditable = decidable && !expired && SIZED_ACTIONS.has(card.action) && proposal.tpLevels.length > 0
  const state = order ? orderStateLabel(order) : cardStateLabel(card.state)
  const value = estimatedValue(card)
  const source = card.providerId ? providerLabel(card.providerId, card.providerName) : "Manual card"
  const signal = signalText(card)
  const disabled = card.state === "disabled" || card.cardType === "disabled" || proposal.disabledReason != null

  return (
    <article
      tabIndex={0}
      aria-label={`${source}, ${card.action} ${card.underlying ?? ""}`.trim()}
      data-card-id={card.id}
      data-provider-id={card.providerId ?? ""}
      data-action={card.action}
      data-underlying={card.underlying ?? ""}
      className="flex flex-col gap-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/60"
    >
      <header className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <h1 className="font-heading text-2xl font-semibold tracking-tight break-words">
            <Badge className={cn("mr-2 align-middle text-xs font-bold tracking-wide uppercase", TONE_CLASS[action.tone])}>
              {action.label}
            </Badge>
            {confirmed ? (
              <Inferred show={inferred.has("underlying")}>{confirmed}</Inferred>
            ) : (
              <span className="text-warning-foreground">Unknown underlying</span>
            )}
          </h1>
          <div className="flex shrink-0 flex-col items-end gap-1">
            {card.state === "disabled" ? null : (
              <Badge className={cn("bg-transparent text-xs", TONE_CLASS[state.tone])}>{state.label}</Badge>
            )}
            {card.state === "pending" ? <Countdown validUntil={card.validUntil} /> : null}
          </div>
        </div>
        <TradeIdentity card={card} />
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span>{source}</span>
          <span aria-hidden>·</span>
          <time dateTime={card.postedAt}>Posted {formatStamp(card.postedAt)}</time>
          {proposal.labels.map((label) => (
            <Badge key={label} className="bg-transparent px-1.5 py-0 text-xs text-warning-foreground">
              {labelBadge(label)}
            </Badge>
          ))}
        </p>
      </header>

      {suggested ? (
        <p role="status" className="text-sm">
          <span className="font-medium">Review required.</span>{" "}
          <span className="text-muted-foreground">
            The underlying is not confirmed. {suggested} is a suggestion, shown apart from the trade fields.
          </span>
        </p>
      ) : null}

      {group === "blocked" ? (
        <p role="note" className="text-sm">
          <span className="font-medium">{disabled ? "Disabled" : "Stale"}</span>
          <span className="text-muted-foreground"> · Approve is unavailable. </span>
          <span className="text-muted-foreground">{blockedReason(card, expired)}</span>
        </p>
      ) : (
        <RiskSummary card={card} />
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4" aria-label="Trade">
        <Field label="Underlying">
          {confirmed ? <Inferred show={inferred.has("underlying")}>{confirmed}</Inferred> : <Missing>Not confirmed</Missing>}
        </Field>
        {suggested ? (
          <Field label="Suggested">
            <span className="font-normal text-warning-foreground">{suggested}</span>
            <span className="mt-0.5 block text-xs font-normal text-muted-foreground">Suggested, not validated</span>
          </Field>
        ) : null}
        {option ? (
          <>
            <Field label="Right">
              {card.contract?.right ? (
                <Inferred show={inferred.has("right")}>{words(card.contract.right)}</Inferred>
              ) : (
                <Missing>Not in signal</Missing>
              )}
            </Field>
            <Field label="Strike">
              {card.contract?.strike ? (
                <Inferred show={inferred.has("strike")}>{formatStrike(card.contract.strike)}</Inferred>
              ) : (
                <Missing>Not in signal</Missing>
              )}
            </Field>
            <Field label="Expiration">
              {card.contract?.expiry ? (
                <Inferred show={inferred.has("expiry_token")}>
                  <time dateTime={card.contract.expiry}>{formatExpiry(card.contract.expiry)}</time>
                </Inferred>
              ) : (
                <Missing>Not in signal</Missing>
              )}
            </Field>
          </>
        ) : null}
        {stock ? (
          <Field label="Instrument">
            Stock
          </Field>
        ) : null}
        {!option && !stock ? (
          <Field label="Contract">
            <Missing>No contract parsed</Missing>
          </Field>
        ) : null}
        {qtyEditable ? null : (
          <Field label="Qty">
            {proposal.qty ?? <Missing>Not stated</Missing>}
          </Field>
        )}
        <Field label={option ? "Premium" : "Price"}>
          {proposal.price == null ? <Missing>No price in signal</Missing> : money(proposal.price)}
        </Field>
        {qtyEditable ? null : (
          <Field label={option ? "Est. premium" : "Est. value"}>
            {value == null ? <Missing>Needs qty and price</Missing> : `~${money(value)}`}
          </Field>
        )}
        <Field label="Provider">{source}</Field>
      </dl>

      <TradeTargets card={card} showLegs={!legsEditable} />
      {decidable && !expired ? <PreflightVerdict cardId={card.id} /> : null}
      <AdvisoryNote note={card.note} hidden={card.noteHidden} />

      <section aria-label="Original message" className="flex flex-col gap-1">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Original message</h2>
        {signal ? (
          <blockquote className="border-l-2 border-border pl-3 text-sm break-words whitespace-pre-wrap">{signal}</blockquote>
        ) : (
          <p className="text-sm text-muted-foreground">No source text on this card.</p>
        )}
      </section>

      {actions}
      {!decidable && group !== "blocked" ? (
        <p role="note" className="text-sm text-muted-foreground">
          This card is {state.label}. Approve is not available.
        </p>
      ) : null}

      <CardDetails card={card} order={order} history={card.events} approval={card.approval} />
    </article>
  )
}
