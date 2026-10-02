"use client"

import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"

import { Input } from "@/components/ui/input"

import { useState, type FormEvent } from "react"
import { Dialog } from "@/components/dialog"
import { Button } from "@/components/ui/button"
import { patchPosition, postPosition, useManualMutation } from "@/features/manual/api"
import { Field, FormError } from "@/features/manual/fields"
import type { PositionDetail } from "@/features/positions/types"
import type { Schemas } from "@/lib/api/types"

export type CorrectionMode = "add" | "edit" | "close"

const TITLES: Record<CorrectionMode, string> = { add: "Add position", edit: "Edit position", close: "Close position" }

type Form = {
  instrument: "option" | "stock"
  underlying: string
  expiry: string
  strike: string
  right: "call" | "put"
  entryPrice: string
  stopLevel: string
  stopBasis: "premium" | "underlying"
  tpLevels: string
  reason: string
}

function initial(mode: CorrectionMode, position: PositionDetail): Form {
  const own = mode === "edit"
  return {
    instrument: position.instrument === "stock" ? "stock" : "option",
    underlying: own ? position.underlying : "",
    expiry: own ? (position.expiry ?? "") : "",
    strike: own ? (position.strike ?? "") : "",
    right: position.optionRight === "put" ? "put" : "call",
    entryPrice: own ? (position.providerEntryPrice ?? "") : "",
    stopLevel: own ? (position.stopLevel ?? "") : "",
    stopBasis: position.stopBasis === "underlying" ? "underlying" : "premium",
    tpLevels: own ? position.tpLevels.join(", ") : "",
    reason: "",
  }
}

const numbers = (text: string) => text.split(/[\s,]+/).filter(Boolean)
const same = (a: string | null | undefined, b: string) => Number(a) === Number(b) && (a ?? "") !== "" && b !== ""

/**
 * Only the fields that changed are sent on edit (wire names: `right`, `entryPrice`).
 * Emptying a field does not clear it: the server treats absent as "unchanged".
 */
function editBody(form: Form, position: PositionDetail): Schemas["EditPositionRequest"] {
  const body: Schemas["EditPositionRequest"] = { reason: form.reason.trim() }
  if (form.underlying.trim() && form.underlying.trim() !== position.underlying) body.underlying = form.underlying.trim()
  if (form.expiry && form.expiry !== position.expiry) body.expiry = form.expiry
  if (form.strike && !same(position.strike, form.strike)) body.strike = form.strike
  if (form.instrument === "option" && form.right !== position.optionRight) body.right = form.right
  if (form.entryPrice && !same(position.providerEntryPrice, form.entryPrice)) body.entryPrice = form.entryPrice
  if (form.stopLevel && !same(position.stopLevel, form.stopLevel)) body.stopLevel = form.stopLevel
  if (form.stopLevel && form.stopBasis !== position.stopBasis) body.stopBasis = form.stopBasis
  const tp = numbers(form.tpLevels)
  if (tp.length > 0 && tp.join(",") !== position.tpLevels.map(String).join(",")) body.tpLevels = tp
  return body
}

/** Add a position for this provider, edit this one, or close it (`status: "closed"`). */
export function CorrectionDialog({
  mode,
  position,
  onClose,
}: {
  mode: CorrectionMode
  position: PositionDetail
  onClose: () => void
}) {
  const [form, setForm] = useState(() => initial(mode, position))
  const set = (patch: Partial<Form>) => setForm((current) => ({ ...current, ...patch }))
  const save = useManualMutation({
    call: (): Promise<unknown> => {
      const reason = form.reason.trim()
      if (mode === "close") return patchPosition(position.id, { status: "closed", reason })
      if (mode === "edit") return patchPosition(position.id, editBody(form, position))
      return postPosition({
        // Snowflake: sent as a string so no digits are lost (the server parses it as an int).
        providerId: position.providerId as unknown as number,
        // The lane (channel + provider) the new position belongs to: the template position's.
        channelId: position.openedChannelId as unknown as number,
        instrument: form.instrument,
        underlying: form.underlying.trim().toUpperCase(),
        reason,
        ...(form.instrument === "option" ? { expiry: form.expiry || null, strike: form.strike || null, right: form.right } : {}),
        ...(form.entryPrice ? { entryPrice: form.entryPrice } : {}),
      })
    },
    success: mode === "close" ? "Position closed" : mode === "add" ? "Position added" : "Position updated",
    onDone: onClose,
  })
  const fields = mode !== "close"
  const isOption = form.instrument === "option"
  const valid =
    form.reason.trim() !== "" && (mode !== "add" || (form.underlying.trim() !== "" && position.openedChannelId != null))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) save.mutate(undefined)
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={TITLES[mode]}
      description={
        mode === "close"
          ? "Marks the provider's position closed. Lots and orders are not changed."
          : mode === "add"
            ? `For provider ${position.providerId} in channel ${position.openedChannelId ?? "unknown"}.`
            : "Only fields you change are saved."
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        {mode === "add" ? (
          <Field label="Instrument">
            {(id) => (
              <NativeSelect id={id} value={form.instrument} onChange={(e) => set({ instrument: e.target.value as Form["instrument"] })}>
                <NativeSelectOption value="option">Option</NativeSelectOption>
                <NativeSelectOption value="stock">Stock</NativeSelectOption>
              </NativeSelect>
            )}
          </Field>
        ) : null}
        {fields ? (
          <>
            <Field label="Underlying">{(id) => <Input id={id} value={form.underlying} onChange={(e) => set({ underlying: e.target.value })} />}</Field>
            {isOption ? (
              <>
                <Field label="Expiry">{(id) => <Input id={id} type="date" value={form.expiry} onChange={(e) => set({ expiry: e.target.value })} />}</Field>
                <Field label="Strike">{(id) => <Input id={id} type="number" inputMode="decimal" min={0} step="any" value={form.strike} onChange={(e) => set({ strike: e.target.value })} />}</Field>
                <Field label="Right">
                  {(id) => (
                    <NativeSelect id={id} value={form.right} onChange={(e) => set({ right: e.target.value as Form["right"] })}>
                      <NativeSelectOption value="call">Call</NativeSelectOption>
                      <NativeSelectOption value="put">Put</NativeSelectOption>
                    </NativeSelect>
                  )}
                </Field>
              </>
            ) : null}
            <Field label="Entry price">{(id) => <Input id={id} type="number" inputMode="decimal" min={0} step="any" value={form.entryPrice} onChange={(e) => set({ entryPrice: e.target.value })} />}</Field>
          </>
        ) : null}
        {mode === "edit" ? (
          <>
            <Field label="Stop level">{(id) => <Input id={id} type="number" inputMode="decimal" min={0} step="any" value={form.stopLevel} onChange={(e) => set({ stopLevel: e.target.value })} />}</Field>
            <Field label="Stop basis">
              {(id) => (
                <NativeSelect id={id} value={form.stopBasis} onChange={(e) => set({ stopBasis: e.target.value as Form["stopBasis"] })}>
                  <NativeSelectOption value="premium">Option premium</NativeSelectOption>
                  <NativeSelectOption value="underlying">Underlying price</NativeSelectOption>
                </NativeSelect>
              )}
            </Field>
            <Field label="Take-profit levels" hint="Comma separated.">
              {(id) => <Input id={id} value={form.tpLevels} onChange={(e) => set({ tpLevels: e.target.value })} />}
            </Field>
          </>
        ) : null}
        <Field label="Reason">{(id) => <Input id={id} value={form.reason} onChange={(e) => set({ reason: e.target.value })} />}</Field>
        <FormError error={save.error} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant={mode === "close" ? "destructive" : "default"} disabled={!valid || save.isPending}>
            {save.isPending ? "Saving…" : mode === "close" ? "Close position" : "Save"}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
