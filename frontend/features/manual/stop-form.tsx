"use client"

import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { postStop, useManualMutation, type StopBody } from "@/features/manual/api"
import { Field, FormError } from "@/features/manual/fields"
import type { LotDetail } from "@/features/lots/types"

type Op = StopBody["op"]
const OPS: { value: Op; label: string }[] = [
  { value: "add", label: "Add stop" },
  { value: "move", label: "Move stop" },
  { value: "cancel", label: "Cancel stop" },
]

/** Manual stop add / move / cancel. The server refuses stop ops on foreign lots, so the caller never renders this for one. */
export function StopForm({ lot }: { lot: LotDetail }) {
  const [op, setOp] = useState<Op>("add")
  const [level, setLevel] = useState("")
  const [basis, setBasis] = useState<NonNullable<StopBody["basis"]>>("premium")
  const stop = useManualMutation({
    call: (body: StopBody, key) => postStop(body, key),
    success: "Stop change submitted",
    onDone: () => setLevel(""),
  })
  const needsLevel = op !== "cancel"
  const valid = !needsLevel || Number(level) > 0

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    stop.mutate({
      lotId: lot.id,
      op,
      ...(needsLevel ? { level: level.trim() } : {}),
      ...(op === "add" ? { basis } : {}),
    })
  }

  return (
    <form onSubmit={submit} aria-label="Stop" className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <h3 className="font-medium">Stop</h3>
      <Field label="Stop operation">
        {(id) => (
          <NativeSelect id={id} value={op} onChange={(e) => setOp(e.target.value as Op)}>
            {OPS.map((o) => (
              <NativeSelectOption key={o.value} value={o.value}>
                {o.label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        )}
      </Field>
      {needsLevel ? (
        <Field label="Stop level">
          {(id) => (
            <Input
              id={id}
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={level}
              onChange={(e) => setLevel(e.target.value)}
            />
          )}
        </Field>
      ) : null}
      {op === "add" ? (
        <Field label="Stop basis">
          {(id) => (
            <NativeSelect id={id} value={basis} onChange={(e) => setBasis(e.target.value as typeof basis)}>
              <NativeSelectOption value="premium">Option premium</NativeSelectOption>
              <NativeSelectOption value="underlying">Underlying price</NativeSelectOption>
            </NativeSelect>
          )}
        </Field>
      ) : null}
      <FormError error={stop.error} />
      <Button type="submit" variant="outline" disabled={!valid || stop.isPending} className="self-start">
        {stop.isPending ? "Submitting…" : "Submit stop change"}
      </Button>
    </form>
  )
}
