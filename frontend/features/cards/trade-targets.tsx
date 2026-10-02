import { money } from "@/features/cards/card-state"
import type { Card } from "@/features/cards/types"

function stopText(card: Card): string | null {
  const { stop, breakevenOptions } = card.proposal
  if (!stop) return null
  if (stop.cancel) return "cancel the current stop"
  if (stop.level == null) return breakevenOptions ? "breakeven, you choose the price" : "level not set"
  return [
    money(stop.level),
    stop.basis ? `on ${stop.basis}` : "",
    stop.heldBy ? `held by ${stop.heldBy}` : "",
    stop.candleClose ? "(candle close, information only)" : "",
  ]
    .filter(Boolean)
    .join(" ")
}

/**
 * "Targets $1.90 · $2.50" and "Stop $1.20", only for what the signal has. With `showLegs` off the
 * take-profit levels are in the editable split in the actions instead.
 */
export function TradeTargets({ card, showLegs }: { card: Card; showLegs: boolean }) {
  const { tpLegs } = card.proposal
  const stop = stopText(card)
  const levels = showLegs ? tpLegs : []
  if (levels.length === 0 && stop == null) return null

  return (
    <dl aria-label="Targets and stop" className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {levels.length > 0 ? (
        <>
          <dt className="text-muted-foreground">Targets</dt>
          <dd className="font-medium tabular-nums">
            {levels.map((leg) => money(leg.level)).join(" · ")}
          </dd>
        </>
      ) : null}
      {stop != null ? (
        <>
          <dt className="text-muted-foreground">Stop</dt>
          <dd className="font-medium tabular-nums">{stop}</dd>
        </>
      ) : null}
    </dl>
  )
}
