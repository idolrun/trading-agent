"use client"

import { useEffect, useRef, useState } from "react"
import { BracketLegs, legsProblem, parseLegs } from "@/features/cards/bracket-legs"
import { BreakevenChoice } from "@/features/cards/breakeven-choice"
import { labelHint } from "@/features/cards/labels"
import { emptyReviewDraft, ReviewFields, reviewEdits } from "@/features/cards/review-fields"
import { isDecidable } from "@/features/cards/types"
import type { BreakevenPick, Card, CardDecision, CardEdits } from "@/features/cards/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type Props = {
  card: Card
  /** This card's decision is in flight. */
  pending: boolean
  /** Another card's decision is in flight: one request at a time. */
  blocked?: boolean
  /** The server's 422 message about this card's fields. */
  error?: string | null
  onDecide: (decision: CardDecision) => void
}

/** Actions with a quantity the user can see and change. */
const QTY_ACTIONS = new Set(["entry", "add", "trim", "exit"])
const SIZED_ACTIONS = new Set(["entry", "add"])

/**
 * Approve / reject / review completion for one pending card. Drafts start from the card and
 * reset when the server returns a new version. The buttons follow what the server says
 * (`missingFields`, `disabledReason`, a blank `qty`); every rule is still enforced there.
 */
export function CardActions(props: Props) {
  if (!isDecidable(props.card)) return null
  return <ActionsForm key={props.card.version} {...props} />
}

function ActionsForm({ card, pending, blocked = false, error = null, onDecide }: Props) {
  const { proposal } = card
  const root = useRef<HTMLDivElement>(null)
  const [qty, setQty] = useState(proposal.qty == null ? "" : String(proposal.qty))
  const [legs, setLegs] = useState(() => proposal.tpLegs.map((leg) => String(leg.qty)))
  const [breakeven, setBreakeven] = useState<BreakevenPick | "">("")
  const [review, setReview] = useState(emptyReviewDraft)

  const hasQty = QTY_ACTIONS.has(card.action)
  const qtyText = qty.trim()
  const qtyValue = /^\d+$/.test(qtyText) && Number(qtyText) >= 1 ? Number(qtyText) : null
  const qtyMalformed = qtyText !== "" && qtyValue == null
  const legsEditable = SIZED_ACTIONS.has(card.action) && proposal.tpLegs.length > 0
  const legsError = legsEditable ? legsProblem(legs, qtyValue) : null
  const legsChanged = legs.some((leg, index) => Number(leg) !== proposal.tpLegs[index]?.qty)
  const missing = proposal.missingFields.length > 0
  const pickBreakeven =
    proposal.breakevenOptions != null && proposal.stop?.level == null && !proposal.missingFields.includes("stop_level")
  const idle = !pending && !blocked

  const canApprove =
    idle &&
    !missing &&
    (!hasQty || qtyValue != null) &&
    legsError == null &&
    (!pickBreakeven || breakeven !== "")

  const qtyEdit: CardEdits = hasQty && qtyValue != null && qtyValue !== proposal.qty ? { qty: qtyValue } : {}

  const approve = () => {
    const edits: CardEdits = { ...qtyEdit }
    if (legsEditable && legsChanged) edits.tpSplit = parseLegs(legs)
    if (pickBreakeven && breakeven) edits.breakevenChoice = breakeven
    onDecide({
      decision: "approve",
      version: card.version,
      edits: Object.keys(edits).length > 0 ? edits : undefined,
    })
  }
  const reject = () => onDecide({ decision: "reject", version: card.version })

  const completion = reviewEdits(card, review)
  const completeReview = () =>
    onDecide({ decision: "edit", version: card.version, edits: { ...completion.edits, ...qtyEdit } })

  // A / R on a focused card. Bound to the card element (the parent article) so focus anywhere in
  // the card counts; typing in a field never triggers it.
  const latest = useRef({ canApprove, idle, approve, reject })
  useEffect(() => {
    latest.current = { canApprove, idle, approve, reject }
  })
  useEffect(() => {
    const article = root.current?.closest("article")
    if (!article) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return
      if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable='true']")) return
      const now = latest.current
      const key = event.key.toLowerCase()
      if (key === "a" && now.canApprove) now.approve()
      else if (key === "r" && now.idle) now.reject()
    }
    article.addEventListener("keydown", onKeyDown)
    return () => article.removeEventListener("keydown", onKeyDown)
  }, [])

  const hints = qtyValue == null ? proposal.labels.map(labelHint).filter((hint) => hint != null) : []
  const buttonClass = "h-12 min-h-11 text-base"

  return (
    <div ref={root} className="flex flex-col gap-4 border-t border-border pt-3">
      {hasQty ? (
        <div className="flex flex-col gap-1">
          <label className="flex items-center justify-between gap-3 text-sm font-medium">
            Quantity
            <input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              className="h-11 w-28 rounded-md border border-input bg-background px-2 text-right tabular-nums"
              value={qty}
              aria-invalid={qtyMalformed}
              aria-describedby={`qty-help-${card.id}`}
              onChange={(event) => setQty(event.target.value)}
            />
          </label>
          <div id={`qty-help-${card.id}`} className="flex flex-col gap-0.5 text-sm">
            {hints.map((hint) => (
              <p key={hint} className="font-medium">
                {hint}
              </p>
            ))}
            {qtyMalformed ? (
              <p role="alert" className="text-destructive">
                Enter a whole number of contracts, 1 or more.
              </p>
            ) : qtyValue == null ? (
              <p className="text-muted-foreground">Enter a quantity to approve.</p>
            ) : null}
          </div>
        </div>
      ) : null}
      <BracketLegs
        cardId={card.id}
        legs={proposal.tpLegs}
        qty={qtyValue}
        values={legsEditable ? legs : undefined}
        onChange={legsEditable ? setLegs : undefined}
      />
      {pickBreakeven && proposal.breakevenOptions ? (
        <BreakevenChoice
          cardId={card.id}
          options={proposal.breakevenOptions}
          value={breakeven}
          onChange={setBreakeven}
        />
      ) : null}
      {missing ? (
        <>
          <ReviewFields card={card} draft={review} onChange={setReview} />
          <Button
            type="button"
            variant="secondary"
            className={buttonClass}
            disabled={!idle || !completion.complete || qtyMalformed}
            onClick={completeReview}
          >
            Complete review
          </Button>
          <p className="text-sm text-muted-foreground">
            Approve unlocks once the review is complete.
          </p>
        </>
      ) : null}
      {pending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Deciding…
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          className={cn(buttonClass, "flex-1")}
          disabled={!idle}
          aria-keyshortcuts="R"
          onClick={reject}
        >
          Reject
          <kbd aria-hidden className="ml-2 hidden rounded border border-border px-1.5 font-mono text-xs sm:inline">
            R
          </kbd>
        </Button>
        <Button
          type="button"
          className={cn(buttonClass, "flex-[2]")}
          disabled={!canApprove}
          aria-keyshortcuts="A"
          onClick={approve}
        >
          Approve
          <kbd aria-hidden className="ml-2 hidden rounded border border-primary-foreground/40 px-1.5 font-mono text-xs sm:inline">
            A
          </kbd>
        </Button>
      </div>
    </div>
  )
}
