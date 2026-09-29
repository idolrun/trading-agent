import { BreakevenChoice } from "@/features/cards/breakeven-choice"
import { words } from "@/features/cards/types"
import type { BreakevenPick, Card, CardEdits } from "@/features/cards/types"

export type ReviewDraft = {
  underlyingConfirmed: boolean
  chosenPositionId: string
  price: string
  expiry: string
  breakeven: BreakevenPick | ""
}

export const emptyReviewDraft: ReviewDraft = {
  underlyingConfirmed: false,
  chosenPositionId: "",
  price: "",
  expiry: "",
  breakeven: "",
}

/**
 * The edit each `missingFields` item is answered by (the server's own pairing, README §4).
 * A field this client has no input for is left out, so the review cannot be completed here.
 */
export function reviewEdits(card: Card, draft: ReviewDraft) {
  const edits: CardEdits = {}
  let complete = true
  for (const field of card.proposal.missingFields) {
    switch (field) {
      case "underlying_confirmation":
        if (draft.underlyingConfirmed && card.proposal.suggestedUnderlying) {
          edits.confirmedUnderlying = card.proposal.suggestedUnderlying
        } else complete = false
        break
      case "chosen_position_id":
        if (draft.chosenPositionId) edits.chosenPositionId = draft.chosenPositionId
        else complete = false
        break
      case "entry_price":
        if (draft.price.trim()) edits.price = draft.price.trim()
        else complete = false
        break
      case "expiry":
        if (draft.expiry) edits.expiry = draft.expiry
        else complete = false
        break
      case "stop_level":
        if (draft.breakeven) edits.breakevenChoice = draft.breakeven
        else complete = false
        break
      default:
        complete = false
    }
  }
  return { edits, complete }
}

const inputClass = "h-11 rounded-md border border-input bg-background px-2"

/** 7.1 step 5: one input per missing field. Nothing is preselected: the user supplies each answer. */
export function ReviewFields({
  card,
  draft,
  onChange,
}: {
  card: Card
  draft: ReviewDraft
  onChange: (draft: ReviewDraft) => void
}) {
  const { proposal } = card
  const set = (patch: Partial<ReviewDraft>) => onChange({ ...draft, ...patch })

  return (
    <fieldset className="flex flex-col gap-3 rounded-md border border-border p-3">
      <legend className="px-1 text-sm font-medium">Complete the review</legend>
      {proposal.missingFields.map((field) => {
        switch (field) {
          case "underlying_confirmation":
            return proposal.suggestedUnderlying ? (
              <label key={field} className="flex min-h-11 items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  className="size-5"
                  checked={draft.underlyingConfirmed}
                  onChange={(event) => set({ underlyingConfirmed: event.target.checked })}
                />
                <span>
                  I confirm the underlying is <strong>{proposal.suggestedUnderlying}</strong>
                </span>
              </label>
            ) : (
              <p key={field} className="text-sm text-muted-foreground">
                No underlying could be suggested for this signal. Reject it, or wait for the provider.
              </p>
            )
          case "chosen_position_id":
            return (
              <fieldset key={field} className="flex flex-col gap-2">
                <legend className="mb-1 text-sm">Which position does this apply to?</legend>
                {proposal.candidatePositions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No open position to choose from.</p>
                ) : null}
                {proposal.candidatePositions.map((id) => (
                  <label
                    key={id}
                    className="flex min-h-11 items-center gap-3 rounded-md border border-border px-3 text-sm has-checked:border-primary has-checked:bg-muted"
                  >
                    <input
                      type="radio"
                      name={`position-${card.id}`}
                      className="size-4"
                      checked={draft.chosenPositionId === id}
                      onChange={() => set({ chosenPositionId: id })}
                    />
                    <span>Position {id.slice(0, 8)}</span>
                  </label>
                ))}
              </fieldset>
            )
          case "entry_price":
            return (
              <label key={field} className="flex flex-col gap-1 text-sm">
                Entry price
                <input
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  className={inputClass}
                  value={draft.price}
                  onChange={(event) => set({ price: event.target.value })}
                />
              </label>
            )
          case "expiry":
            return (
              <label key={field} className="flex flex-col gap-1 text-sm">
                Expiry
                <input
                  type="date"
                  className={inputClass}
                  value={draft.expiry}
                  onChange={(event) => set({ expiry: event.target.value })}
                />
              </label>
            )
          case "stop_level":
            return proposal.breakevenOptions ? (
              <BreakevenChoice
                key={field}
                cardId={card.id}
                options={proposal.breakevenOptions}
                value={draft.breakeven}
                onChange={(breakeven) => set({ breakeven })}
              />
            ) : (
              <p key={field} className="text-sm text-muted-foreground">
                The stop level is missing and cannot be entered here.
              </p>
            )
          default:
            return (
              <p key={field} className="text-sm text-muted-foreground">
                {words(field)} is missing and cannot be entered here. Reject the card, or wait for the
                provider.
              </p>
            )
        }
      })}
    </fieldset>
  )
}
