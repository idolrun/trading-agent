"use client"

import { AlertTriangle, Ban, Check } from "lucide-react"
import { Badge } from "@/components/badge"
import { money, TONE_CLASS } from "@/features/cards/card-state"
import type { Tone } from "@/features/cards/card-state"
import { usePreflight } from "@/features/cards/queries"
import { Field } from "@/features/cards/trade-field"
import { words } from "@/features/cards/types"
import type { Preflight } from "@/features/cards/queries"
import { cn } from "@/lib/utils"

/** What each server check is called to a trader. The status itself always comes from the server. */
const CHECK_LABELS: Record<string, string> = {
  hours: "Market hours",
  validity: "Card still valid",
  contract: "Contract known",
  quote: "Quote available",
  quote_fresh: "Quote is recent",
  quote_valid: "Quote is not crossed",
  price_band: "Price within the entry band",
  superseded: "No newer provider message",
  risk: "Account and risk limits",
  position: "Position still open",
  lot: "Lot quantity",
  broker_position: "Broker holds the quantity",
}

const STATUS: Record<string, { label: string; tone: Tone; icon: typeof Check }> = {
  pass: { label: "PASS", tone: "neutral", icon: Check },
  review: { label: "REVIEW", tone: "warning", icon: AlertTriangle },
  blocked: { label: "BLOCKED", tone: "destructive", icon: Ban },
}

const status = (value: string) => STATUS[value] ?? STATUS.review

/** `a → b`, or just `a` while the projection is unknown. */
function change(now: string | null | undefined, after: string | null | undefined) {
  if (now == null) return money(after)
  return after == null ? money(now) : `${money(now)} → ${money(after)}`
}

function Numbers({ pf }: { pf: Preflight }) {
  const exposure = pf.maxExposure == null ? null : ` of ${money(pf.maxExposure)}`
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
      <Field label="Bid / ask">
        {money(pf.bid)} / {money(pf.ask)}
      </Field>
      <Field label="Proposed price">{money(pf.limitPrice)}</Field>
      <Field label="Est. cost">{pf.estCost == null ? "—" : money(pf.estCost)}</Field>
      <Field label="Buying power">{change(pf.buyingPower, pf.projectedBuyingPower)}</Field>
      <Field label="Exposure">
        {change(pf.exposure, pf.projectedExposure)}
        {exposure}
      </Field>
      <Field label="Equity / cash">
        {money(pf.equity)} / {money(pf.cash)}
      </Field>
      <Field label="Daily P&L">
        {money(pf.dailyPnl)}
        {pf.maxDailyLoss == null ? "" : ` (limit −${money(pf.maxDailyLoss)})`}
      </Field>
      {pf.openPositions != null ? (
        <Field label="Open positions">
          {pf.openPositions}
          {pf.maxOpenPositions == null ? "" : ` of ${pf.maxOpenPositions}`}
        </Field>
      ) : null}
    </dl>
  )
}

/**
 * The server's live checks for a card the user is deciding on: each check PASS / REVIEW /
 * BLOCKED, and the numbers behind them. It restates what revalidation will look at; the rules
 * live on the server, and Approve is revalidated there again whatever this shows.
 */
export function PreflightSummary({ cardId }: { cardId: string }) {
  const { data: pf, isError, isPending } = usePreflight(cardId, true)
  if (isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Checking quote, account and limits…
      </p>
    )
  }
  if (isError || !pf) {
    return (
      <p role="alert" className="text-sm text-warning-foreground">
        Live checks unavailable. Approval is still revalidated on the server.
      </p>
    )
  }
  if (!pf.applicable) return null
  const verdict = status(pf.verdict)
  return (
    <section aria-label="Live checks" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium">Live checks</h4>
        <Badge className={cn("bg-transparent font-semibold", TONE_CLASS[verdict.tone])}>{verdict.label}</Badge>
      </div>
      <ul className="flex flex-col gap-1">
        {pf.checks.map((check) => {
          const s = status(check.status)
          return (
            <li key={check.name} className="flex items-start gap-2 text-sm">
              <s.icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", TONE_CLASS[s.tone])} />
              <span className="min-w-0">
                {CHECK_LABELS[check.name] ?? words(check.name)}{" "}
                <span className={cn("text-xs font-semibold", TONE_CLASS[s.tone])}>{s.label}</span>
                {check.status !== "pass" && (check.detail ?? check.reason) ? (
                  <span className="text-muted-foreground">
                    {" "}
                    {check.detail ?? words(check.reason ?? "")}
                  </span>
                ) : null}
              </span>
            </li>
          )
        })}
      </ul>
      <Numbers pf={pf} />
    </section>
  )
}

/**
 * The face of a card: one line with the server's verdict, and only the checks that are not
 * PASS. The full list and the numbers live in Details (`PreflightSummary`).
 */
export function PreflightVerdict({ cardId }: { cardId: string }) {
  const { data: pf, isError, isPending } = usePreflight(cardId, true)
  if (isPending) {
    return (
      <p role="status" className="text-xs text-muted-foreground">
        Checking quote, account and limits…
      </p>
    )
  }
  if (isError || !pf) {
    return (
      <p role="alert" className="text-xs text-warning-foreground">
        Live checks unavailable. Approval is still revalidated on the server.
      </p>
    )
  }
  if (!pf.applicable) return null
  const verdict = status(pf.verdict)
  const issues = pf.checks.filter((check) => check.status !== "pass")
  return (
    <div aria-label="Risk check" role="group" className="flex flex-col gap-0.5 text-sm">
      <p className="flex items-center gap-1.5">
        <verdict.icon aria-hidden className={cn("size-4 shrink-0", TONE_CLASS[verdict.tone])} />
        <span className="font-medium">Risk check</span>
        <span className={cn("text-xs font-semibold", TONE_CLASS[verdict.tone])}>{verdict.label}</span>
      </p>
      {issues.map((check) => (
        <p key={check.name} className="pl-5.5 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{CHECK_LABELS[check.name] ?? words(check.name)}</span>
          {check.detail ?? check.reason ? `: ${check.detail ?? words(check.reason ?? "")}` : ""}
        </p>
      ))}
    </div>
  )
}
