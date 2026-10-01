"use client"

import Link from "next/link"
import { useState, type FormEvent } from "react"
import { Dialog } from "@/components/dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { StepUpDialog } from "@/features/auth/step-up-dialog"
import { useStepUp } from "@/features/auth/use-step-up"
import { words } from "@/features/cards/types"
import { postFlatten, useManualMutation, type FlattenResult, type FlattenScope } from "@/features/manual/api"
import { FormError } from "@/features/manual/fields"

const SCOPES: { value: FlattenScope; label: string }[] = [
  { value: "options", label: "Options only" },
  { value: "stocks", label: "Stocks only" },
  { value: "all", label: "Everything" },
]

/** A refused lot is a normal part of the result (HTTP 200), not an error. */
function FlattenSummary({ result }: { result: FlattenResult }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>{result.killSwitch ? "Kill switch is on." : "Kill switch is off."}</p>
      {result.lots.length === 0 ? <p className="text-muted-foreground">No open lots in this scope.</p> : null}
      <ul className="flex flex-col gap-1">
        {result.lots.map((lot) => (
          <li key={lot.cardId} className="rounded-md border border-border px-3 py-2">
            <Link href={lot.lotId ? `/lots/${lot.lotId}` : `/cards/${lot.cardId}`} className="underline underline-offset-2">
              Lot {lot.lotId ?? "unknown"}
            </Link>
            {lot.blocked ? (
              <p className="text-destructive">Blocked: {words(lot.blocked)}</p>
            ) : (
              <p className="text-muted-foreground">
                {words(lot.state)} · {lot.orderIds.length} order{lot.orderIds.length === 1 ? "" : "s"}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function FlattenDialog({ onClose }: { onClose: () => void }) {
  const stepUp = useStepUp()
  const [scope, setScope] = useState<FlattenScope>("options")
  const flatten = useManualMutation({
    call: (value: FlattenScope, key) => postFlatten(value, key),
    withStepUp: stepUp.withStepUp,
    success: "Flatten submitted",
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    flatten.mutate(scope)
  }

  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => !open && onClose()}
        title="Flatten positions"
        description="Turns the kill switch on and sends an exit for every open lot in scope. Needs a password check."
      >
        {flatten.data ? (
          <>
            <FlattenSummary result={flatten.data} />
            <Button type="button" variant="outline" onClick={onClose} className="self-end">
              Close
            </Button>
          </>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-3">
            <fieldset className="flex flex-col gap-1">
              <legend className="text-sm font-medium">Scope</legend>
              <RadioGroup value={scope} onValueChange={(value) => setScope(value as FlattenScope)} className="gap-1">
                {SCOPES.map((option) => (
                  <Label key={option.value} className="min-h-11 gap-3 font-normal leading-normal select-text">
                    <RadioGroupItem value={option.value} />
                    {option.label}
                  </Label>
                ))}
              </RadioGroup>
            </fieldset>
            <FormError error={flatten.error} />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" variant="destructive" disabled={flatten.isPending}>
                {flatten.isPending ? "Flattening…" : "Flatten now"}
              </Button>
            </div>
          </form>
        )}
      </Dialog>
      <StepUpDialog {...stepUp.dialogProps} />
    </>
  )
}

export function FlattenControl() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button type="button" size="sm" variant="destructive" onClick={() => setOpen(true)}>
        Flatten
      </Button>
      {open ? <FlattenDialog onClose={() => setOpen(false)} /> : null}
    </>
  )
}
