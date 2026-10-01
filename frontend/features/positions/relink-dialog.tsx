"use client"

import { Input } from "@/components/ui/input"

import { useState, type FormEvent } from "react"
import { Dialog } from "@/components/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { useCardList } from "@/features/cards/queries"
import { words } from "@/features/cards/types"
import { postLink, useManualMutation } from "@/features/manual/api"
import { Field, FormError } from "@/features/manual/fields"
import type { PositionDetail } from "@/features/positions/types"
import { formatContract, formatTime } from "@/lib/format"

/**
 * Link one of this provider's signals to this position. Candidates are the provider's
 * recent cards that carry a signal id.
 * ponytail: first page only; add "Load more" if the signal is older.
 */
export function RelinkDialog({ position, onClose }: { position: PositionDetail; onClose: () => void }) {
  // Provider ids are snowflakes: a string on the wire, typed `number` by the generator.
  const cards = useCardList({ providerId: position.providerId as unknown as number })
  const [signalId, setSignalId] = useState("")
  const [reason, setReason] = useState("")
  const relink = useManualMutation({
    call: (body: { signalId: string; reason: string }) => postLink({ ...body, providerPositionId: position.id }),
    success: "Signal linked",
    onDone: onClose,
  })

  const candidates = (cards.data?.items ?? []).filter((card) => card.signalId && card.providerId === position.providerId)
  const valid = signalId !== "" && reason.trim() !== ""

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) relink.mutate({ signalId, reason: reason.trim() })
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Re-link signal"
      description="Pick the signal that belongs to this position."
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium">Signal</legend>
          {cards.isPending ? <p className="text-sm text-muted-foreground">Loading signals</p> : null}
          {cards.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {cards.error.message}
            </p>
          ) : null}
          {cards.data && candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recent signals from this provider.</p>
          ) : null}
          <RadioGroup value={signalId} onValueChange={setSignalId} className="gap-1">
            {candidates.map((card) => (
              <Label key={card.id} className="min-h-11 gap-3 font-normal leading-normal select-text">
                <RadioGroupItem value={card.signalId!} />
                <span>
                  {words(card.action)} {card.contract ? formatContract(card.contract) : (card.underlying ?? "")}
                  <span className="block text-xs text-muted-foreground">{formatTime(card.postedAt)}</span>
                </span>
              </Label>
            ))}
          </RadioGroup>
        </fieldset>
        <Field label="Reason">{(id) => <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
        <FormError error={relink.error} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!valid || relink.isPending}>
            {relink.isPending ? "Linking…" : "Link signal"}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
