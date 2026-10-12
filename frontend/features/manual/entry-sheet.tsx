"use client"

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { signalText } from "@/features/cards/card-summary"
import type { Card } from "@/features/cards/types"
import { EntryForm } from "@/features/manual/entry-form"
import { guessEntry, type EntryDraft } from "@/features/manual/guess"
import { formatStamp, providerLabel } from "@/lib/format"

/** What the card and its message already say: the card's own fields win over a guess from the text. */
export function draftFromCard(card: Card, today = new Date()): Partial<EntryDraft> {
  const draft = guessEntry(signalText(card) ?? "", today)
  const { contract } = card
  const underlying = contract?.underlying ?? card.underlying ?? card.proposal.suggestedUnderlying
  if (underlying) draft.underlying = underlying
  if (contract?.instrument === "stock") draft.instrument = "stock"
  if (contract?.instrument === "option") {
    draft.instrument = "option"
    if (contract.expiry) draft.expiry = contract.expiry
    if (contract.strike) draft.strike = contract.strike
    if (contract.right === "call" || contract.right === "put") draft.right = contract.right
  }
  const price = card.proposal.price ?? card.proposal.signalPrice
  if (price) draft.limitPrice = price
  return draft
}

/**
 * Manual entry for a signal the system could not read, beside the list it came from: the message
 * stays in view while the user types, and nothing navigates away. `onDone` runs once accepted.
 */
export function EntrySheet({
  card,
  open,
  onOpenChange,
  onDone,
}: {
  card: Card
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const text = signalText(card)
  const author = card.providerId ? providerLabel(card.providerId, card.providerName) : "Manual card"
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-md">
        <SheetHeader className="gap-3 border-b border-border">
          <div className="flex flex-col gap-1 pr-8">
            <SheetTitle className="text-base">Enter trade</SheetTitle>
            <SheetDescription>
              {author} · <time dateTime={card.postedAt}>{formatStamp(card.postedAt)}</time>
            </SheetDescription>
          </div>
          <blockquote className="max-h-32 overflow-y-auto rounded-md border-l-2 border-primary bg-muted/40 px-3 py-2 text-sm [overflow-wrap:anywhere] whitespace-pre-wrap">
            {text ?? <span className="text-muted-foreground italic">(no text)</span>}
          </blockquote>
        </SheetHeader>
        <div className="p-4">
          {/* Keyed on the card: another card's sheet starts from its own message. */}
          <EntryForm key={card.id} initial={draftFromCard(card)} onDone={onDone} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
