"use client"

import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { StepUpDialog } from "@/features/auth/step-up-dialog"
import { useStepUp } from "@/features/auth/use-step-up"
import { postAdjust, useManualMutation } from "@/features/manual/api"
import { Field, FormError, inputClass } from "@/features/manual/fields"
import type { LotDetail } from "@/features/lots/types"

/** Reconcile our lot quantity to what the broker shows (step-up). Qty 0 closes the lot. */
export function AdjustLot({ lot }: { lot: LotDetail }) {
  const stepUp = useStepUp()
  const [qty, setQty] = useState(String(lot.qty))
  const [reason, setReason] = useState("")
  const adjust = useManualMutation({
    call: (body: { qty: number; reason: string }) => postAdjust(lot.id, body),
    withStepUp: stepUp.withStepUp,
    success: "Lot quantity adjusted",
    onDone: () => setReason(""),
  })
  const n = Number(qty)
  const valid = qty.trim() !== "" && Number.isInteger(n) && n >= 0 && reason.trim().length > 0

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) adjust.mutate({ qty: n, reason: reason.trim() })
  }

  return (
    <>
    <form onSubmit={submit} aria-label="Adjust" className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <h3 className="font-medium">Adjust to broker quantity</h3>
      <Field label="Broker qty" hint="Setting 0 closes this lot. Needs a password check.">
        {(id) => (
          <input
            id={id}
            type="number"
            inputMode="numeric"
            min={0}
            step={1}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className={inputClass}
          />
        )}
      </Field>
      <Field label="Adjust reason">
        {(id) => <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} className={inputClass} />}
      </Field>
      <FormError error={adjust.error} />
      <Button type="submit" variant="outline" disabled={!valid || adjust.isPending} className="self-start">
        {adjust.isPending ? "Adjusting…" : "Adjust quantity"}
      </Button>
    </form>
    <StepUpDialog {...stepUp.dialogProps} />
    </>
  )
}
