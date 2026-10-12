import { AlertTriangle, Ban, Check } from "lucide-react"
import type { ReactNode } from "react"
import { words } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"

/** What a missing field means to the trader, one phrase each. */
function missingText(card: Card, field: string): string {
  const { proposal } = card
  switch (field) {
    case "underlying_confirmation":
      return proposal.suggestedUnderlying
        ? `confirm the underlying (${proposal.suggestedUnderlying})`
        : "underlying not identified"
    case "chosen_position_id":
      return proposal.candidatePositions.length > 0 ? "choose the matching position" : "no matching open position"
    case "entry_price":
      return "entry price missing"
    case "expiry":
      return "expiry missing"
    case "stop_level":
      return "stop level missing"
    default:
      return `${words(field).toLowerCase()} missing`
  }
}

function Row({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <span aria-hidden className="mt-0.5 shrink-0 [&>svg]:size-4">
        {icon}
      </span>
      <p>
        <span className="font-medium">{title}</span>
        {children}
      </p>
    </div>
  )
}

/**
 * One status row: disabled, review needed, caution (warnings and labels the server put on the
 * proposal) or no flags. Only for a card still waiting on the user; later states have nothing to
 * decide. This restates what the card carries and adds no rule of its own.
 */
export function RiskSummary({ card }: { card: Card }) {
  const { proposal } = card
  if (card.state !== "pending" && card.state !== "disabled") return null

  const missing = proposal.missingFields.map((field) => missingText(card, field))
  const flagged = proposal.warnings.length > 0 || proposal.labels.length > 0

  return (
    <section aria-label="Risk" className="flex flex-col gap-1">
      {proposal.disabledReason || card.cardType === "disabled" ? (
        <Row icon={<Ban className="text-destructive" />} title="Disabled">
          {proposal.disabledReason ? <span className="text-muted-foreground"> {proposal.disabledReason}</span> : null}
        </Row>
      ) : missing.length > 0 || card.cardType === "review" ? (
        <Row icon={<AlertTriangle className="text-warning-foreground" />} title="Review needed">
          {missing.length > 0 ? (
            <span className="text-muted-foreground"> {missing.join("; ")}</span>
          ) : null}
        </Row>
      ) : flagged ? null : (
        <Row icon={<Check className="text-muted-foreground" />} title="No risk flags" />
      )}
      {proposal.warnings.map((warning) => (
        <Row key={warning} icon={<AlertTriangle className="text-warning-foreground" />} title={warning} />
      ))}
    </section>
  )
}
