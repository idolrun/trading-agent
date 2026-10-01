import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
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
      <RadioGroup name={`breakeven-${cardId}`} value={value} onValueChange={(id) => onChange(id as BreakevenPick)} className="gap-2">
        {choices.map((choice) => (
          <Label
            key={choice.id}
            className="min-h-11 gap-3 rounded-md border border-border px-3 font-normal leading-normal select-text has-data-[state=checked]:border-primary has-data-[state=checked]:bg-muted has-data-[disabled]:opacity-50"
          >
            <RadioGroupItem value={choice.id} disabled={choice.price == null} />
            <span>
              {choice.label}{" "}
              <span className="tabular-nums text-muted-foreground">
                {choice.price == null ? "(unknown)" : formatPrice(choice.price)}
              </span>
            </span>
          </Label>
        ))}
      </RadioGroup>
    </fieldset>
  )
}
