"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { Card, CardDecision } from "@/features/cards/types"

export function CardActions({
  card,
  pending,
  onDecide,
}: {
  card: Card
  pending: boolean
  onDecide: (decision: CardDecision) => void
}) {
  const [qtyDraft, setQtyDraft] = useState(
    card.proposal.qty == null ? "" : String(card.proposal.qty),
  )
  const [draftError, setDraftError] = useState<string | null>(null)
  const canDecide = card.state === "pending" && card.cardType !== "disabled"
  if (!canDecide) return null

  const needsQty = card.action === "trim" && card.proposal.qty == null

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3">
      {card.action === "trim" ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            const qty = Number(qtyDraft)
            if (!Number.isFinite(qty) || qty <= 0) {
              setDraftError("Enter a quantity greater than zero.")
              return
            }
            setDraftError(null)
            onDecide({ decision: "edit", edits: { qty }, version: card.version })
          }}
        >
          <label className="flex flex-col gap-1 text-sm" htmlFor={`qty-${card.id}`}>
            Quantity
            <input
              id={`qty-${card.id}`}
              inputMode="decimal"
              value={qtyDraft}
              onChange={(event) => setQtyDraft(event.target.value)}
              className="h-8 w-24 rounded-md border border-input bg-background px-2"
            />
          </label>
          <Button type="submit" size="sm" variant="outline" disabled={pending}>
            Save quantity
          </Button>
        </form>
      ) : null}
      {draftError ? (
        <p role="alert" className="text-sm text-destructive">
          {draftError}
        </p>
      ) : null}
      {needsQty ? (
        <p className="text-sm text-muted-foreground">Save a quantity before approving.</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => onDecide({ decision: "reject", version: card.version })}
        >
          Reject
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={pending || needsQty}
          onClick={() => onDecide({ decision: "approve", version: card.version })}
        >
          Approve
        </Button>
      </div>
    </div>
  )
}
