"use client"

import Link from "next/link"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card as Shell, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { QTY_ACTIONS } from "@/features/cards/card-actions"
import { money, orderValue, qtyUnit, side, TONE_CLASS } from "@/features/cards/card-state"
import { signalText } from "@/features/cards/card-summary"
import { useExpired } from "@/features/cards/countdown"
import { isUnreadable } from "@/features/cards/types"
import { EntrySheet } from "@/features/manual/entry-sheet"
import type { Card, CardDecision } from "@/features/cards/types"
import { formatContract, formatStamp, providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

type Props = {
  card: Card
  pending: boolean
  blocked: boolean
  error: string | null
  onDecide: (decision: CardDecision) => void
}

/**
 * One pending card at triage size: what, who, when, the signal, a quantity, Reject / Approve.
 * An unreadable signal's Approve opens the entry sheet in place. Anything else needing more than a
 * quantity (fields to confirm, a breakeven pick) opens the full card; the server enforces every rule.
 */
export function CompactCard({ card, pending, blocked, error, onDecide }: Props) {
  const expired = useExpired(card.validUntil)
  const [qty, setQty] = useState("")
  const [choice, setChoice] = useState<"approve" | "reject" | null>(null)
  const [entering, setEntering] = useState(false)
  const { proposal } = card
  const action = side(card.action)
  const author = card.providerId ? providerLabel(card.providerId, card.providerName) : "Manual card"
  const signal = signalText(card)
  const unit = qtyUnit(card)
  const hasQty = QTY_ACTIONS.has(card.action)
  const qtyValue = /^\d+$/.test(qty.trim()) && Number(qty) >= 1 ? Number(qty) : null
  const qtyMalformed = qty.trim() !== "" && qtyValue == null
  const unreadable = !expired && isUnreadable(card)
  const needsFullCard =
    expired ||
    proposal.missingFields.length > 0 ||
    (proposal.breakevenOptions != null && proposal.stop?.level == null)
  const idle = !pending && !blocked
  const value = orderValue(card, qtyValue)
  const helpId = `cr-help-${card.id}`

  const decide = (decision: CardDecision) => {
    setChoice(decision.decision === "reject" ? "reject" : "approve")
    onDecide(decision)
  }
  const approve = () =>
    decide({ decision: "approve", version: card.version, edits: hasQty && qtyValue != null ? { qty: qtyValue } : undefined })

  return (
    <Shell
      role="article"
      tabIndex={0}
      aria-label={`${author}, ${card.action} ${card.underlying ?? ""}`.trim()}
      data-card-id={card.id}
      className="gap-0 py-0 outline-none focus-visible:ring-3 focus-visible:ring-ring/60"
    >
      <CardContent className="flex flex-col gap-2 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className={cn("shrink-0 rounded-md border px-1.5 text-xs font-bold uppercase", TONE_CLASS[action.tone])}>
            {action.label}
          </span>
          <Link
            href={`/cards/${card.id}`}
            className="min-w-0 truncate text-sm font-semibold underline-offset-2 hover:underline"
          >
            {card.contract ? formatContract(card.contract) : (card.underlying ?? "Unknown")}
          </Link>
          <time dateTime={card.postedAt} className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
            {formatStamp(card.postedAt)}
          </time>
        </div>
        <p className="truncate text-xs text-muted-foreground">{author}</p>
        {signal ? (
          <p className="line-clamp-2 text-sm [overflow-wrap:anywhere] whitespace-pre-wrap">{signal}</p>
        ) : null}
        {hasQty && !needsFullCard && !unreadable ? (
          <div className="flex items-center gap-2">
            <label htmlFor={`cr-qty-${card.id}`} className="text-xs font-medium">
              Qty
            </label>
            <Input
              id={`cr-qty-${card.id}`}
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={qty}
              onChange={(event) => setQty(event.target.value)}
              aria-invalid={qtyMalformed}
              aria-describedby={helpId}
              className="h-9 w-20 text-right tabular-nums"
            />
            <span className="text-xs text-muted-foreground">{unit}</span>
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">
              {value == null ? null : `~${money(value)}`}
            </span>
          </div>
        ) : null}
        <div id={helpId} className="text-xs">
          {qtyMalformed ? <p className="text-destructive">Whole number of {unit}, 1 or more.</p> : null}
          {error ? (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-9 flex-1 border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={!idle}
            onClick={() => decide({ decision: "reject", version: card.version })}
          >
            {pending && choice === "reject" ? "Rejecting…" : "Reject"}
          </Button>
          {unreadable ? (
            <Button type="button" className="h-9 flex-[2]" disabled={!idle} onClick={() => setEntering(true)}>
              Approve
            </Button>
          ) : needsFullCard ? (
            <Button asChild variant="secondary" className="h-9 flex-[2]">
              <Link href={`/cards/${card.id}`}>{expired ? "Expired: open" : "Open to review"}</Link>
            </Button>
          ) : (
            <Button
              type="button"
              className="h-9 flex-[2]"
              disabled={!idle || (hasQty && qtyValue == null)}
              onClick={approve}
            >
              {pending && choice === "approve" ? "Approving…" : card.action === "close" ? "Close all" : "Approve"}
            </Button>
          )}
        </div>
      </CardContent>
      {unreadable ? (
        <EntrySheet
          card={card}
          open={entering}
          onOpenChange={setEntering}
          onDone={() => {
            setEntering(false)
            decide({ decision: "reject", version: card.version }) // the raw card clears itself
          }}
        />
      ) : null}
    </Shell>
  )
}
