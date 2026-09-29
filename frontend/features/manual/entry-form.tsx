"use client"

import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { StepUpDialog } from "@/features/auth/step-up-dialog"
import { useStepUp } from "@/features/auth/use-step-up"
import { postEntry, useManualMutation, type EntryBody } from "@/features/manual/api"
import { Field, FormError, inputClass } from "@/features/manual/fields"

type Instrument = EntryBody["instrument"]

const EMPTY = { instrument: "option", underlying: "", expiry: "", strike: "", right: "call", qty: "1", limitPrice: "" }

/** Manual entry (step-up). The server runs the shared entry gates and its refusal is shown inline. */
export function EntryForm() {
  const stepUp = useStepUp()
  const [form, setForm] = useState(EMPTY)
  const set = (patch: Partial<typeof EMPTY>) => setForm((current) => ({ ...current, ...patch }))
  const entry = useManualMutation({
    call: (body: EntryBody, key) => postEntry(body, key),
    withStepUp: stepUp.withStepUp,
    success: "Entry submitted",
    onDone: () => setForm(EMPTY),
  })

  const isOption = form.instrument === "option"
  const qty = Number(form.qty)
  const valid =
    form.underlying.trim() !== "" &&
    Number.isInteger(qty) &&
    qty >= 1 &&
    Number(form.limitPrice) > 0 &&
    (!isOption || (form.expiry !== "" && Number(form.strike) > 0))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    const instrument: Instrument = isOption
      ? {
          instrument: "option",
          underlying: form.underlying.trim().toUpperCase(),
          expiry: form.expiry,
          strike: form.strike.trim(),
          right: form.right as "call" | "put",
        }
      : { instrument: "stock", underlying: form.underlying.trim().toUpperCase() }
    entry.mutate({ instrument, qty, limitPrice: form.limitPrice.trim() })
  }

  return (
    <>
      <form onSubmit={submit} aria-label="Manual entry" className="flex flex-col gap-3">
        <Field label="Instrument">
          {(id) => (
            <select id={id} value={form.instrument} onChange={(e) => set({ instrument: e.target.value })} className={inputClass}>
              <option value="option">Option</option>
              <option value="stock">Stock</option>
            </select>
          )}
        </Field>
        <Field label="Underlying">
          {(id) => (
            <input
              id={id}
              value={form.underlying}
              autoCapitalize="characters"
              onChange={(e) => set({ underlying: e.target.value })}
              className={inputClass}
            />
          )}
        </Field>
        {isOption ? (
          <>
            <Field label="Expiry">
              {(id) => (
                <input id={id} type="date" value={form.expiry} onChange={(e) => set({ expiry: e.target.value })} className={inputClass} />
              )}
            </Field>
            <Field label="Strike">
              {(id) => (
                <input
                  id={id}
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  value={form.strike}
                  onChange={(e) => set({ strike: e.target.value })}
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Right">
              {(id) => (
                <select id={id} value={form.right} onChange={(e) => set({ right: e.target.value })} className={inputClass}>
                  <option value="call">Call</option>
                  <option value="put">Put</option>
                </select>
              )}
            </Field>
          </>
        ) : null}
        <Field label="Quantity">
          {(id) => (
            <input
              id={id}
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={form.qty}
              onChange={(e) => set({ qty: e.target.value })}
              className={inputClass}
            />
          )}
        </Field>
        <Field label="Limit price" hint="Needs a password check.">
          {(id) => (
            <input
              id={id}
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={form.limitPrice}
              onChange={(e) => set({ limitPrice: e.target.value })}
              className={inputClass}
            />
          )}
        </Field>
        <FormError error={entry.error} />
        <Button type="submit" disabled={!valid || entry.isPending} className="self-start">
          {entry.isPending ? "Submitting…" : "Submit entry"}
        </Button>
      </form>
      <StepUpDialog {...stepUp.dialogProps} />
    </>
  )
}
