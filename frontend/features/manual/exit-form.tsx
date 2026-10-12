"use client"

import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { postExit, useManualMutation } from "@/features/manual/api"
import { Field, FormError } from "@/features/manual/fields"
import type { LotDetail } from "@/features/lots/types"

/** Manual exit (FR-27): sells `qty` of this lot. Allowed for foreign lots too. */
export function ExitForm({ lot }: { lot: LotDetail }) {
  const [qty, setQty] = useState("") // the user types it; "Flatten" is the whole-position close
  const exit = useManualMutation({
    call: (n: number, key) => postExit({ lotId: lot.id, qty: n }, key),
    success: "Exit submitted",
  })
  const n = Number(qty)
  const valid = Number.isInteger(n) && n >= 1 && n <= lot.qty

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) exit.mutate(n)
  }

  return (
    <form onSubmit={submit} aria-label="Exit" className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <h3 className="font-medium">Exit</h3>
      <Field label="Exit qty" hint={`Required, up to ${lot.qty}`}>
        {(id) => (
          <Input
            id={id}
            type="number"
            inputMode="numeric"
            min={1}
            max={lot.qty}
            step={1}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
        )}
      </Field>
      <FormError error={exit.error} order />
      <Button type="submit" variant="outline" disabled={!valid || exit.isPending} className="self-start">
        {exit.isPending ? "Exiting…" : "Exit"}
      </Button>
    </form>
  )
}
