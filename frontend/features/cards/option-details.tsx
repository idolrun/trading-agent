import { Inferred } from "@/features/cards/trade-field"
import { words } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"

/** "2026-10-16" as a calendar date, without a time-zone shift. */
function formatExpiry(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number)
  if (!year || !month || !day) return iso
  return new Date(year, month - 1, day).toLocaleDateString("en-US", { dateStyle: "medium" })
}

/** "$120" / "$357.5": a strike is a round number more often than a price. */
const formatStrike = (strike: string) =>
  `$${Number(strike).toLocaleString("en-US", { maximumFractionDigits: 4 })}`

/**
 * One line under the title: "$120 Call · Sep 25, 2026". A non-entry action leads it
 * ("Exit · $120 Call · Sep 25, 2026"). A plain stock entry has nothing to add.
 */
export function TradeIdentity({ card }: { card: Card }) {
  const { contract } = card
  const inferred = new Set(card.inferredFields.map((item) => item.field))
  const lead = card.action === "entry" || card.action === "add" ? null : words(card.action)
  const option = contract?.instrument === "option"

  const parts = [
    lead && <span key="lead">{lead}</span>,
    option && contract.strike && (
      <span key="strike">
        <Inferred show={inferred.has("strike")}>{formatStrike(contract.strike)}</Inferred>
        {contract.right ? (
          <>
            {" "}
            <Inferred show={inferred.has("right")}>{words(contract.right)}</Inferred>
          </>
        ) : null}
      </span>
    ),
    option && contract.expiry && (
      <span key="expiry">
        <Inferred show={inferred.has("expiry_token")}>
          <time dateTime={contract.expiry}>{formatExpiry(contract.expiry)}</time>
        </Inferred>
      </span>
    ),
  ].filter(Boolean)
  if (parts.length === 0) return null

  return (
    <p role="group" aria-label="Contract" className="mt-0.5 flex flex-wrap gap-x-2 text-sm font-medium text-muted-foreground">
      {parts.map((part, index) => (
        <span key={index} className="flex gap-2">
          {index > 0 ? <span aria-hidden className="text-muted-foreground">·</span> : null}
          {part}
        </span>
      ))}
    </p>
  )
}
