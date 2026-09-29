"use client"

import { AlertTriangle, Ban, CircleHelp, ClipboardCheck, Lightbulb } from "lucide-react"
import Link from "next/link"
import type { ReactNode } from "react"
import { AdvisoryNote } from "@/features/cards/advisory-note"
import { BracketLegs } from "@/features/cards/bracket-legs"
import { Countdown, useExpired } from "@/features/cards/countdown"
import { labelBadge } from "@/features/cards/labels"
import { SourceMessages } from "@/features/cards/source-messages"
import { isDecidable, words } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"
import { formatPrice } from "@/lib/format"
import { cn } from "@/lib/utils"

function Badge({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <li className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium">
      {icon}
      {children}
    </li>
  )
}

/** Inferred fields are marked in the contract text itself (dotted underline + "inferred" title). */
function ContractText({ card }: { card: Card }) {
  const inferred = new Set(card.inferredFields.map((item) => item.field))
  const { contract } = card
  if (!contract) return <>{card.underlying ?? "Unknown underlying"}</>

  const parts = [
    { key: "underlying", text: contract.underlying, field: "underlying" },
    ...(contract.instrument === "option"
      ? [
          { key: "expiry", text: contract.expiry, field: "expiry_token" },
          { key: "strike", text: contract.strike, field: "strike" },
          { key: "right", text: contract.right, field: "right" },
        ]
      : []),
  ].filter((part) => part.text)

  return (
    <>
      {parts.map((part, index) => (
        <span key={part.key}>
          {index > 0 ? " " : null}
          {inferred.has(part.field) ? (
            <mark
              title="Inferred from an earlier message"
              className="rounded-sm bg-amber-200 px-0.5 text-amber-950 underline decoration-dotted dark:bg-amber-400/30 dark:text-amber-100"
            >
              {part.text}
            </mark>
          ) : (
            part.text
          )}
        </span>
      ))}
    </>
  )
}

function StopLine({ card }: { card: Card }) {
  const { stop, breakevenOptions } = card.proposal
  if (!stop) return null
  if (stop.cancel) return <p className="text-sm">Stop: cancel the current stop</p>
  if (stop.level == null) {
    return <p className="text-sm">Stop: {breakevenOptions ? "breakeven, you choose the price" : "level not set"}</p>
  }
  return (
    <p className="text-sm tabular-nums">
      Stop {formatPrice(stop.level)}
      {stop.basis ? ` on ${stop.basis}` : ""}
      {stop.heldBy ? `, held by ${stop.heldBy}` : ""}
      {stop.candleClose ? " (candle close, information only)" : ""}
    </p>
  )
}

export function CardSummary({
  card,
  linked = true,
  actions,
}: {
  card: Card
  linked?: boolean
  actions?: ReactNode
}) {
  const { proposal } = card
  const expired = useExpired(card.validUntil)
  const decidable = isDecidable(card)
  const author = card.providerId ? `Provider ${card.providerId}` : "Manual card"
  const title = (
    <>
      <span className="capitalize">{card.action}</span> <ContractText card={card} />
    </>
  )

  return (
    <article
      tabIndex={0}
      aria-label={`${author}, ${card.action} ${card.underlying ?? ""}`.trim()}
      data-card-id={card.id}
      data-expired={expired}
      className={cn(
        "flex flex-col gap-3 rounded-lg border border-border bg-card p-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        expired && "opacity-60 grayscale",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-lg font-semibold break-words">{author}</p>
        <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
          <Countdown validUntil={card.validUntil} />
          {card.state !== "pending" && card.state !== "disabled" ? (
            <p className="text-sm text-muted-foreground capitalize">{card.state}</p>
          ) : null}
        </div>
      </div>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="min-w-0 text-base font-medium">
          {linked ? (
            <Link href={`/cards/${card.id}`} className="hover:underline">
              {title}
            </Link>
          ) : (
            title
          )}
        </h3>
        <p className="shrink-0 text-base font-medium tabular-nums">
          {proposal.qty ?? "—"} × {formatPrice(proposal.price)}
        </p>
      </div>
      <ul aria-label="Labels" className="flex flex-wrap gap-1.5 empty:hidden">
        {card.cardType === "review" ? (
          <Badge icon={<ClipboardCheck aria-hidden className="size-3.5" />}>Review</Badge>
        ) : null}
        {card.cardType === "inferred" ? (
          <Badge icon={<CircleHelp aria-hidden className="size-3.5" />}>Inferred</Badge>
        ) : null}
        {card.cardType === "disabled" ? (
          <Badge icon={<Ban aria-hidden className="size-3.5" />}>Disabled</Badge>
        ) : null}
        {proposal.labels.map((label) => (
          <Badge key={label} icon={<Lightbulb aria-hidden className="size-3.5" />}>
            {labelBadge(label)}
          </Badge>
        ))}
      </ul>
      {proposal.disabledReason ? (
        <p role="note" className="flex items-start gap-2 rounded-md border border-border bg-muted px-3 py-2 text-sm">
          <Ban aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <span className="font-medium">Disabled: </span>
            {proposal.disabledReason}
          </span>
        </p>
      ) : null}
      {proposal.reviewReasons.length > 0 ? (
        <ul aria-label="Review reasons" className="text-sm text-muted-foreground">
          {proposal.reviewReasons.map((reason) => (
            <li key={reason}>Review: {words(reason)}</li>
          ))}
        </ul>
      ) : null}
      {proposal.warnings.length > 0 ? (
        <ul aria-label="Warnings" className="flex flex-col gap-1 text-sm">
          {proposal.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-2">
              <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
              {warning}
            </li>
          ))}
        </ul>
      ) : null}
      <StopLine card={card} />
      {decidable ? null : (
        <BracketLegs cardId={card.id} legs={proposal.tpLegs} qty={proposal.qty} />
      )}
      <AdvisoryNote note={card.note} hidden={card.noteHidden} />
      <SourceMessages card={card} />
      {actions}
    </article>
  )
}
