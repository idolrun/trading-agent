import { Input } from "@/components/ui/input"
import { formatPrice } from "@/lib/format"
import type { CardProposal } from "@/features/cards/types"

type Legs = CardProposal["tpLegs"]

/** Whole contracts >= 1: the input's own rule. Whether a split is acceptable stays with the server. */
export function parseLegs(values: string[]): number[] | null {
  const legs = values.map((value) => (/^\d+$/.test(value.trim()) ? Number(value) : NaN))
  return legs.every((leg) => leg >= 1) ? legs : null
}

/**
 * AC-16: legs never exceed the order quantity and add up to it. Returns the message to show
 * while the split cannot be submitted, or null when it can.
 */
export function legsProblem(values: string[], qty: number | null): string | null {
  const legs = parseLegs(values)
  if (legs == null) return "Each take-profit leg needs at least 1 contract."
  if (qty == null) return null
  if (legs.some((leg) => leg > qty)) return `A leg cannot be larger than the order quantity (${qty}).`
  const total = legs.reduce((sum, leg) => sum + leg, 0)
  return total === qty ? null : `Legs add up to ${total}; they must add up to ${qty}.`
}

export function BracketLegs({
  cardId,
  legs,
  qty,
  values,
  onChange,
}: {
  cardId: string
  legs: Legs
  /** Order quantity the legs must add up to; null when the card has none yet. */
  qty: number | null
  /** Omit for a read-only list. */
  values?: string[]
  onChange?: (values: string[]) => void
}) {
  if (legs.length === 0) return null

  if (!values || !onChange) {
    return (
      <ul aria-label="Take profit legs" className="flex flex-col gap-1 text-sm">
        {legs.map((leg, index) => (
          <li key={leg.level + index} className="flex justify-between tabular-nums">
            <span>TP{index + 1} @ {formatPrice(leg.level)}</span>
            <span>{leg.qty}</span>
          </li>
        ))}
      </ul>
    )
  }

  const problem = legsProblem(values, qty)

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium">
        Take-profit split{qty == null ? "" : ` (adds up to ${qty})`}
      </legend>
      {legs.map((leg, index) => (
        <label key={leg.level + index} className="flex items-center justify-between gap-3 text-sm">
          <span className="tabular-nums">TP{index + 1} @ {formatPrice(leg.level)}</span>
          <Input
            type="number"
            inputMode="numeric"
            min={1}
            max={qty ?? undefined}
            step={1}
            aria-label={`TP${index + 1} quantity`}
            aria-invalid={problem != null}
            aria-describedby={problem ? `legs-problem-${cardId}` : undefined}
            className="w-24 text-right tabular-nums"
            value={values[index] ?? ""}
            onChange={(event) => onChange(values.map((v, i) => (i === index ? event.target.value : v)))}
          />
        </label>
      ))}
      {problem ? (
        <p id={`legs-problem-${cardId}`} role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      ) : null}
    </fieldset>
  )
}
