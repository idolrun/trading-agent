"use client"

import { useState, type FormEvent, type ReactNode } from "react"
import { MinusIcon, PlusIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { money } from "@/features/cards/card-state"
import { postEntry, useManualMutation, type EntryBody } from "@/features/manual/api"
import { FormError } from "@/features/manual/fields"
import { EMPTY_DRAFT, expiryShortcuts, type EntryDraft } from "@/features/manual/guess"
import { cn } from "@/lib/utils"

type Instrument = EntryBody["instrument"]

const QTY_PRESETS = [1, 2, 5, 10]
const SEGMENT = "h-11 flex-1 text-sm font-semibold data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"

/**
 * Manual entry. The server runs the shared entry gates and its refusal is shown inline.
 * `initial` prefills fields read from a signal; each one is marked so the user checks it.
 * `onDone` runs after the entry is accepted (an unreadable signal's card uses it to clear itself).
 */
export function EntryForm({ onDone, initial = {} }: { onDone?: () => void; initial?: Partial<EntryDraft> } = {}) {
  const [form, setForm] = useState<EntryDraft>({ ...EMPTY_DRAFT, ...initial })
  const [touched, setTouched] = useState<ReadonlySet<keyof EntryDraft>>(new Set())
  const set = (patch: Partial<EntryDraft>) => {
    setForm((current) => ({ ...current, ...patch }))
    setTouched((current) => new Set([...current, ...(Object.keys(patch) as (keyof EntryDraft)[])]))
  }
  const fromMessage = (key: keyof EntryDraft) => Boolean(initial[key]) && !touched.has(key)
  const entry = useManualMutation({
    call: (body: EntryBody, key) => postEntry(body, key),
    success: "Entry submitted",
    onDone: () => {
      setForm(EMPTY_DRAFT)
      setTouched(new Set())
      onDone?.()
    },
  })

  const isOption = form.instrument === "option"
  const qty = Number(form.qty)
  const qtyValid = Number.isInteger(qty) && qty >= 1
  const price = Number(form.limitPrice)
  const valid =
    form.underlying.trim() !== "" &&
    qtyValid &&
    price > 0 &&
    (!isOption || (form.expiry !== "" && Number(form.strike) > 0))
  const started = form.underlying.trim() !== "" || form.strike !== "" || form.qty !== "" || form.limitPrice !== ""
  const cost = qtyValid && price > 0 ? qty * price * (isOption ? 100 : 1) : null

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    const instrument: Instrument = isOption
      ? {
          instrument: "option",
          underlying: form.underlying.trim().toUpperCase(),
          expiry: form.expiry,
          strike: form.strike.trim(),
          right: form.right,
        }
      : { instrument: "stock", underlying: form.underlying.trim().toUpperCase() }
    entry.mutate({ instrument, qty, limitPrice: form.limitPrice.trim() })
  }

  const step = (delta: number) => set({ qty: String(Math.max(1, (qtyValid ? qty : 0) + delta)) })

  return (
    <>
      <form onSubmit={submit} aria-label="Manual entry" className="grid grid-cols-2 gap-x-3 gap-y-4">
        <Slot label="Instrument" className={isOption ? "" : "col-span-2"}>
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={0}
            className="w-full"
            value={form.instrument}
            onValueChange={(value) => value && set({ instrument: value as EntryDraft["instrument"] })}
            aria-label="Instrument"
          >
            <ToggleGroupItem value="option" className={SEGMENT}>
              Option
            </ToggleGroupItem>
            <ToggleGroupItem value="stock" className={SEGMENT}>
              Stock
            </ToggleGroupItem>
          </ToggleGroup>
        </Slot>
        {isOption ? (
          <Slot label="Right" filled={fromMessage("right")}>
            <ToggleGroup
              type="single"
              variant="outline"
              spacing={0}
              className="w-full"
              value={form.right}
              onValueChange={(value) => value && set({ right: value as EntryDraft["right"] })}
              aria-label="Right"
            >
              <ToggleGroupItem
                value="call"
                className={cn(SEGMENT, "data-[state=on]:bg-success data-[state=on]:text-background")}
              >
                Call
              </ToggleGroupItem>
              <ToggleGroupItem
                value="put"
                className={cn(SEGMENT, "data-[state=on]:bg-destructive data-[state=on]:text-background")}
              >
                Put
              </ToggleGroupItem>
            </ToggleGroup>
          </Slot>
        ) : null}

        <Slot
          label="Underlying"
          htmlFor="entry-underlying"
          filled={fromMessage("underlying")}
          className={isOption ? "" : "col-span-2"}
        >
          <Input
            id="entry-underlying"
            value={form.underlying}
            autoCapitalize="characters"
            autoComplete="off"
            spellCheck={false}
            className="h-11 font-mono text-base uppercase"
            onChange={(e) => set({ underlying: e.target.value })}
          />
        </Slot>
        {isOption ? (
          <Slot label="Strike" htmlFor="entry-strike" filled={fromMessage("strike")}>
            <Input
              id="entry-strike"
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={form.strike}
              className="h-11 text-base tabular-nums"
              onChange={(e) => set({ strike: e.target.value })}
            />
          </Slot>
        ) : null}

        {isOption ? (
          <Slot label="Expiry" htmlFor="entry-expiry" filled={fromMessage("expiry")} className="col-span-2">
            <div className="flex flex-wrap gap-2">
              {expiryShortcuts(new Date()).map((day) => (
                <Button
                  key={day.value}
                  type="button"
                  variant={form.expiry === day.value ? "default" : "outline"}
                  aria-pressed={form.expiry === day.value}
                  className="h-11 flex-1"
                  onClick={() => set({ expiry: day.value })}
                >
                  {day.label}
                </Button>
              ))}
              <Input
                id="entry-expiry"
                type="date"
                value={form.expiry}
                className="h-11 min-w-36 flex-[2] tabular-nums"
                onChange={(e) => set({ expiry: e.target.value })}
              />
            </div>
          </Slot>
        ) : null}

        <Slot label="Quantity" htmlFor="entry-qty" hint={isOption ? "contracts" : "shares"}>
          <InputGroup className="h-11">
            <InputGroupAddon>
              <Button type="button" variant="ghost" size="icon-sm" aria-label="One less" onClick={() => step(-1)}>
                <MinusIcon aria-hidden />
              </Button>
            </InputGroupAddon>
            <InputGroupInput
              id="entry-qty"
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={form.qty}
              className="text-center text-base tabular-nums"
              onChange={(e) => set({ qty: e.target.value })}
            />
            <InputGroupAddon align="inline-end">
              <Button type="button" variant="ghost" size="icon-sm" aria-label="One more" onClick={() => step(1)}>
                <PlusIcon aria-hidden />
              </Button>
            </InputGroupAddon>
          </InputGroup>
          <div className="grid grid-cols-4 gap-1.5">
            {QTY_PRESETS.map((n) => (
              <Button
                key={n}
                type="button"
                size="sm"
                variant={form.qty === String(n) ? "secondary" : "outline"}
                className="h-8 min-w-0 px-0 tabular-nums"
                aria-label={`Quantity ${n}`}
                onClick={() => set({ qty: String(n) })}
              >
                {n}
              </Button>
            ))}
          </div>
        </Slot>
        <Slot
          label="Limit price"
          htmlFor="entry-price"
          filled={fromMessage("limitPrice")}
          hint="per share"
        >
          <InputGroup className="h-11">
            <InputGroupAddon>
              <InputGroupText>$</InputGroupText>
            </InputGroupAddon>
            <InputGroupInput
              id="entry-price"
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={form.limitPrice}
              className="text-base tabular-nums"
              onChange={(e) => set({ limitPrice: e.target.value })}
            />
          </InputGroup>
        </Slot>

        <div
          aria-live="polite"
          className="col-span-2 flex items-baseline justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2.5"
        >
          {started ? (
            <span className="min-w-0 font-mono text-sm [overflow-wrap:anywhere]">{preview(form)}</span>
          ) : (
            <span className="text-sm text-muted-foreground">The order appears here as you fill it.</span>
          )}
          <span className="shrink-0 text-sm tabular-nums">
            <span className="text-muted-foreground">Est. </span>
            <span className="font-semibold">{cost == null ? "—" : money(cost)}</span>
          </span>
        </div>

        <div className="col-span-2 flex flex-col gap-2">
          <FormError error={entry.error} order />
          <Button type="submit" disabled={!valid || entry.isPending} className="h-11 w-full text-sm font-semibold">
            {entry.isPending ? "Submitting…" : "Submit entry"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">Limit buy.</p>
        </div>
      </form>
    </>
  )
}

/** "BUY 2 QQQ 2026-10-12 748C @ 0.42": the order as it will be sent, for a last look. */
function preview(form: EntryDraft): string {
  const qty = form.qty.trim() || "?"
  const symbol = form.underlying.trim().toUpperCase() || "?"
  const price = form.limitPrice.trim() || "?"
  if (form.instrument === "stock") return `BUY ${qty} ${symbol} @ ${price}`
  const strike = form.strike.trim() || "?"
  return `BUY ${qty} ${symbol} ${form.expiry || "?"} ${strike}${form.right === "call" ? "C" : "P"} @ ${price}`
}

function Slot({
  label,
  htmlFor,
  hint,
  filled = false,
  className,
  children,
}: {
  label: string
  htmlFor?: string
  hint?: string
  filled?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-2">
        {/* A toggle group names itself (aria-label); a <label> only points at a real control. */}
        {htmlFor ? (
          <span className="flex flex-wrap items-baseline gap-x-1.5 whitespace-nowrap">
            <label htmlFor={htmlFor} className="text-sm font-medium">
              {label}
            </label>
            {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
          </span>
        ) : (
          <span aria-hidden className="text-sm font-medium">
            {label}
          </span>
        )}
        {filled ? (
          <span className="rounded border border-info/40 bg-info-muted px-1.5 text-xs font-medium text-info-foreground">
            from message
          </span>
        ) : null}
      </div>
      {children}
    </div>
  )
}
