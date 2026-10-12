"use client"

import { Input } from "@/components/ui/input"

import Link from "next/link"
import { useEffect, useRef, useState } from "react"
import { BracketLegs, legsProblem, parseLegs, splitLegs } from "@/features/cards/bracket-legs"
import { BreakevenChoice } from "@/features/cards/breakeven-choice"
import { money, orderValue, qtyUnit } from "@/features/cards/card-state"
import { labelHint } from "@/features/cards/labels"
import { usePreflight } from "@/features/cards/queries"
import { useExpired } from "@/features/cards/countdown"
import { emptyReviewDraft, ReviewFields, reviewEdits } from "@/features/cards/review-fields"
import { isDecidable, isUnreadable, words } from "@/features/cards/types"
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

/** Actions whose quantity the user types (C105). A `close` takes none: it sells the whole position. */
export const QTY_ACTIONS = new Set(["entry", "add", "trim", "exit"])
export const SIZED_ACTIONS = new Set(["entry", "add"])

/**
 * Approve / reject / review completion for one pending card. Drafts start from the card and
 * reset when the server returns a new version. The buttons follow what the server says
 * (`missingFields`, `disabledReason`, a blank `qty`); every rule is still enforced there.
 */
export function CardActions(props: Props) {
  const expired = useExpired(props.card.validUntil)
  if (!isDecidable(props.card)) return null
  // Past its validity window the server refuses an approval, so none is offered: only a dismissal.
  if (expired) return <ExpiredActions {...props} />
  if (isUnreadable(props.card)) return <UnreadableActions {...props} />
  return <ActionsForm key={props.card.version} {...props} />
}

/** The system could not read this signal: no order was made and none can be approved from here. */
function UnreadableActions({ card, pending, blocked = false, error = null, onDecide }: Props) {
  return (
    <div className="flex flex-col gap-2">
      <p role="note" className="text-sm">
        <span className="font-medium">Could not read this signal reliably.</span>{" "}
        <span className="text-muted-foreground">
          No order will be placed. To trade it, use{" "}
          <Link href="/manual/entry" className="underline">
            Manual entry
          </Link>
          .
        </span>
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        className="min-h-11 self-start"
        disabled={pending || blocked}
        onClick={() => onDecide({ decision: "reject", version: card.version })}
      >
        {pending ? "Dismissing…" : "Dismiss"}
      </Button>
    </div>
  )
}

function ExpiredActions({ card, pending, blocked = false, error = null, onDecide }: Props) {
  return (
    <div className="flex flex-col gap-2">
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button
        type="button"
        variant="outline"
        className="min-h-11 self-start"
        disabled={pending || blocked}
        onClick={() => onDecide({ decision: "reject", version: card.version })}
      >
        {pending ? "Rejecting…" : "Reject"}
      </Button>
    </div>
  )
}

/** The preflight checks that depend on the quantity the user typed. */
const QTY_CHECKS = new Set(["risk", "lot", "broker_position"])

/** `value`, once it has held still for `ms` (a live check per keystroke would hammer the broker). */
function useSettled<T>(value: T, ms = 400): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

function ActionsForm({ card, pending, blocked = false, error = null, onDecide }: Props) {
  const { proposal } = card
  const root = useRef<HTMLDivElement>(null)
  // C105: the quantity starts empty and only the user fills it. Nothing from the card seeds it.
  const [qty, setQty] = useState("")
  const [legOverride, setLegOverride] = useState<string[] | null>(null)
  const [breakeven, setBreakeven] = useState<BreakevenPick | "">("")
  const [review, setReview] = useState(emptyReviewDraft)

  const isClose = card.action === "close"
  const hasQty = QTY_ACTIONS.has(card.action)
  const unit = qtyUnit(card)
  const qtyText = qty.trim()
  const qtyValue = /^\d+$/.test(qtyText) && Number(qtyText) >= 1 ? Number(qtyText) : null
  const qtyMalformed = qtyText !== "" && qtyValue == null
  const legsEditable = SIZED_ACTIONS.has(card.action) && proposal.tpLevels.length > 0
  const defaultLegs = splitLegs(proposal.tpLevels, qtyValue)
  const legs = legOverride ?? defaultLegs.map((leg) => String(leg.qty))
  const legsError = legsEditable && qtyValue != null ? legsProblem(legs, qtyValue) : null
  const missing = proposal.missingFields.length > 0
  const pickBreakeven =
    proposal.breakevenOptions != null && proposal.stop?.level == null && !proposal.missingFields.includes("stop_level")
  const idle = !pending && !blocked

  // The server judges the number the user typed (buying power, exposure, the lot, the broker's
  // holding). A refusal is shown as it is: the quantity is never reduced to fit.
  const settled = useSettled(qtyValue)
  const live = usePreflight(card.id, hasQty && settled != null, settled)
  const refusals =
    hasQty && qtyValue != null && live.data?.qty === qtyValue
      ? live.data.checks.filter((check) => QTY_CHECKS.has(check.name) && check.status === "blocked")
      : []

  const canApprove =
    idle &&
    !missing &&
    (!hasQty || qtyValue != null) &&
    refusals.length === 0 &&
    legsError == null &&
    (!pickBreakeven || breakeven !== "")

  // Always the number in the box: an approval carries the user's quantity, never the card's own.
  const qtyEdit: CardEdits = hasQty && qtyValue != null ? { qty: qtyValue } : {}

  const approve = () => {
    const edits: CardEdits = { ...qtyEdit }
    if (legsEditable && legOverride != null) edits.tpSplit = parseLegs(legOverride)
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

  // A / R only while the card itself has focus (Tab to it, or click its empty space). Focus on a
  // button or field inside the card never triggers a decision, so a stray key cannot approve.
  const latest = useRef({ canApprove, idle, approve, reject })
  useEffect(() => {
    latest.current = { canApprove, idle, approve, reject }
  })
  useEffect(() => {
    const article = root.current?.closest<HTMLElement>("article, [role='article']")
    if (!article) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return
      if (event.target !== article) return
      const now = latest.current
      const key = event.key.toLowerCase()
      if (key === "a" && now.canApprove) now.approve()
      else if (key === "r" && now.idle) now.reject()
    }
    article.addEventListener("keydown", onKeyDown)
    return () => article.removeEventListener("keydown", onKeyDown)
  }, [])

  const hints = qtyValue == null ? proposal.labels.map(labelHint).filter((hint) => hint != null) : []
  const buttonClass = "h-11 min-h-11 text-sm font-semibold"

  return (
    <div ref={root} className="flex flex-col gap-2.5">
      {hasQty ? (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor={`qty-${card.id}`} className="text-sm font-medium">
              Quantity
            </label>
            <div className="flex items-center gap-2">
              <Input
                id={`qty-${card.id}`}
                type="number"
                inputMode="numeric"
                min={1}
                step={1}
                className="w-28 text-right tabular-nums"
                value={qty}
                aria-invalid={qtyMalformed || refusals.length > 0}
                aria-required
                aria-describedby={`qty-help-${card.id}`}
                onChange={(event) => {
                  setQty(event.target.value)
                  setLegOverride(null) // the split follows the new quantity
                }}
              />
              <span className="w-16 text-sm text-muted-foreground">{unit}</span>
            </div>
          </div>
          <div id={`qty-help-${card.id}`} className="flex flex-col gap-0.5 text-sm">
            {hints.map((hint) => (
              <p key={hint} className="font-medium">
                {hint}
              </p>
            ))}
            {qtyMalformed ? (
              <p role="alert" className="text-destructive">
                Enter a whole number of {unit}, 1 or more.
              </p>
            ) : qtyValue == null ? (
              <p className="text-muted-foreground">Required: enter the number of {unit} to approve.</p>
            ) : null}
            {refusals.map((check) => (
              <p key={check.name} role="alert" className="text-destructive">
                Not allowed at {qtyValue} {unit}: {check.detail ?? words(check.reason ?? check.name)}. Enter a different
                quantity.
              </p>
            ))}
            <p className="flex justify-between tabular-nums">
              <span className="text-muted-foreground">Est. order value</span>
              <span className="font-medium">
                {orderValue(card, qtyValue) == null ? "—" : `~${money(orderValue(card, qtyValue))}`}
              </span>
            </p>
          </div>
        </div>
      ) : null}
      {legsEditable && defaultLegs.length > 0 ? (
        <BracketLegs cardId={card.id} legs={defaultLegs} qty={qtyValue} values={legs} onChange={setLegOverride} />
      ) : null}
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
            className="min-h-11 self-start"
            disabled={!idle || !completion.complete || qtyMalformed}
            onClick={completeReview}
          >
            Complete review
          </Button>
        </>
      ) : null}
      {missing ? (
        <p className="text-sm text-muted-foreground">Approve stays off until the required fields are confirmed.</p>
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
          className={cn(buttonClass, "flex-1 border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive")}
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
          {isClose ? `Close entire position${proposal.qty == null ? "" : ` (${proposal.qty})`}` : "Approve"}
          <kbd aria-hidden className="ml-2 hidden rounded border border-primary-foreground/40 px-1.5 font-mono text-xs sm:inline">
            A
          </kbd>
        </Button>
      </div>
    </div>
  )
}
