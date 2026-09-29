import { formatPrice } from "@/lib/format"
import type { BreakevenPick, CardProposal } from "@/features/cards/types"

/** C29: the provider said "breakeven"; the user picks which price it means. */
export function BreakevenChoice({
  cardId,
  options,
  value,
  onChange,
}: {
  cardId: string
  options: NonNullable<CardProposal["breakevenOptions"]>
  value: BreakevenPick | ""
  onChange: (value: BreakevenPick) => void
}) {
  const choices: { id: BreakevenPick; label: string; price: string | null }[] = [
    { id: "user_avg", label: "Your average", price: options.userAvg },
    { id: "provider_entry", label: "Provider entry", price: options.providerEntry },
  ]

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium">Breakeven stop at</legend>
      {choices.map((choice) => (
        <label
          key={choice.id}
          className="flex min-h-11 items-center gap-3 rounded-md border border-border px-3 text-sm has-checked:border-primary has-checked:bg-muted has-disabled:opacity-50"
        >
          <input
            type="radio"
            name={`breakeven-${cardId}`}
            className="size-4"
            checked={value === choice.id}
            disabled={choice.price == null}
            onChange={() => onChange(choice.id)}
          />
          <span>
            {choice.label}{" "}
            <span className="tabular-nums text-muted-foreground">
              {choice.price == null ? "(unknown)" : formatPrice(choice.price)}
            </span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}
