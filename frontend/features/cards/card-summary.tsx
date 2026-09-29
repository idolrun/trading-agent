import Link from "next/link"
import type { ReactNode } from "react"
import { formatPrice } from "@/lib/format"
import type { Card } from "@/features/cards/types"

export function CardSummary({
  card,
  linked = true,
  actions,
}: {
  card: Card
  linked?: boolean
  actions?: ReactNode
}) {
  const title = (
    <>
      {card.underlying ?? "Unknown underlying"}
      <span className="font-normal text-muted-foreground"> · {card.action}</span>
    </>
  )

  return (
    <article className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-medium">
            {linked ? (
              <Link href={`/cards/${card.id}`} className="hover:underline">
                {title}
              </Link>
            ) : (
              title
            )}
          </h3>
          <p className="text-sm text-muted-foreground capitalize">
            {card.cardType} · {card.state}{card.providerId ? ` · ${card.providerId}` : ""}
          </p>
        </div>
        <p className="shrink-0 text-sm tabular-nums">
          {card.proposal.qty ?? "—"} @ {formatPrice(card.proposal.price)}
        </p>
      </div>
      {card.proposal.warnings.length > 0 ? (
        <ul className="text-sm text-muted-foreground">
          {card.proposal.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {actions}
    </article>
  )
}
