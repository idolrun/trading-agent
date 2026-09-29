"use client"

import { createContext, useContext, useId, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { INT_FIELDS, type Draft, type ListKey, type RowsKey, type ScalarKey, type ServerErrors } from "@/features/config/types"
import { cn } from "@/lib/utils"

type FormState = {
  draft: Draft
  patch: (changes: Partial<Draft>) => void
  /** Top-level keys the server says are unset (entries disabled), as of the last load/save. */
  missing: ReadonlySet<string>
  errors: ServerErrors
}

const FormContext = createContext<FormState | null>(null)
export const FormProvider = FormContext.Provider

export function useConfigForm(): FormState {
  const state = useContext(FormContext)
  if (!state) throw new Error("useConfigForm outside FormProvider")
  return state
}

export const UNSET_HINT = "not set — entries disabled"
const input = "h-11 rounded-md border bg-background px-2 text-sm"

/** Per-field server errors (422), shown under the field they name. */
export function FieldErrors({ name }: { name: string }) {
  const { errors } = useConfigForm()
  return (
    <>
      {(errors.fields[name] ?? []).map((message) => (
        <p key={message} role="alert" className="text-xs text-destructive">
          {message}
        </p>
      ))}
    </>
  )
}

type Control = { id: string; className: string; "aria-describedby"?: string; "aria-invalid"?: true; "data-unset"?: true }

/**
 * Label + control + hint. A field the server lists as unset, and that is still empty,
 * gets an amber border and the "not set — entries disabled" hint.
 */
export function ConfigField({
  name,
  label,
  hint,
  empty = false,
  className,
  children,
}: {
  name: string
  label: string
  hint?: string
  empty?: boolean
  className?: string
  children: (control: Control) => ReactNode
}) {
  const id = useId()
  const { missing, errors } = useConfigForm()
  const unset = empty && missing.has(name)
  const invalid = (errors.fields[name]?.length ?? 0) > 0
  const describedBy = unset ? `${id}-unset` : undefined
  return (
    <div className={cn("flex flex-col gap-1 text-sm", className)}>
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {children({
        id,
        className: cn(input, invalid ? "border-destructive" : unset ? "border-amber-500 bg-amber-500/5" : "border-input"),
        "aria-describedby": describedBy,
        ...(invalid ? { "aria-invalid": true as const } : {}),
        ...(unset ? { "data-unset": true as const } : {}),
      })}
      {unset ? (
        <p id={describedBy} className="text-xs font-medium text-amber-700 dark:text-amber-400">
          {UNSET_HINT}
        </p>
      ) : null}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      <FieldErrors name={name} />
    </div>
  )
}

/** Integer or decimal input; the draft keeps the text, `toDoc` converts. Empty = null. */
export function ScalarField({ name, label, hint }: { name: ScalarKey; label: string; hint?: string }) {
  const { draft, patch } = useConfigForm()
  const int = (INT_FIELDS as readonly string[]).includes(name)
  return (
    <ConfigField name={name} label={label} hint={hint} empty={draft[name].trim() === ""}>
      {(control) => (
        <input
          {...control}
          type="number"
          inputMode={int ? "numeric" : "decimal"}
          step={int ? 1 : "any"}
          value={draft[name]}
          onChange={(event) => patch({ [name]: event.target.value })}
        />
      )}
    </ConfigField>
  )
}

export function CheckField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex min-h-11 items-center gap-2 text-sm font-medium">
      <input type="checkbox" className="size-4" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  )
}

/** A list of ids (snowflake strings): add one at a time, remove any. */
export function ListField({ name, label }: { name: ListKey; label: string }) {
  const { draft, patch } = useConfigForm()
  const [text, setText] = useState("")
  const items = draft[name]
  const add = () => {
    const value = text.trim()
    if (value && !items.includes(value)) patch({ [name]: [...items, value] })
    setText("")
  }
  return (
    <ConfigField name={name} label={label} className="sm:col-span-2">
      {(control) => (
        <>
          {items.length > 0 ? (
            <ul aria-label={`${label} list`} className="flex flex-wrap gap-2">
              {items.map((item) => (
                <li key={item} className="flex items-center gap-1 rounded-full border border-border bg-muted py-0.5 pr-1 pl-3 text-xs tabular-nums">
                  {item}
                  <button
                    type="button"
                    aria-label={`Remove ${item} from ${label}`}
                    className="inline-flex size-8 items-center justify-center rounded-full hover:bg-background"
                    onClick={() => patch({ [name]: items.filter((other) => other !== item) })}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex gap-2">
            <input
              {...control}
              className={cn(control.className, "flex-1")}
              inputMode="numeric"
              placeholder="Discord id"
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  add()
                }
              }}
            />
            <Button type="button" variant="outline" className="h-11" onClick={add}>
              Add
            </Button>
          </div>
        </>
      )}
    </ConfigField>
  )
}

/** Editable name/value rows (tiers, symbol map). Rows left blank are dropped on save. */
export function RowsField({
  name,
  label,
  nameLabel,
  valueLabel,
  numeric = false,
  hint,
}: {
  name: RowsKey
  label: string
  nameLabel: string
  valueLabel: string
  numeric?: boolean
  hint?: string
}) {
  const { draft, patch } = useConfigForm()
  const rows = draft[name]
  const set = (next: typeof rows) => patch({ [name]: next })
  const edit = (index: number, change: Partial<(typeof rows)[number]>) =>
    set(rows.map((row, at) => (at === index ? { ...row, ...change } : row)))
  const empty = rows.every((row) => row.value.trim() === "")
  return (
    <ConfigField name={name} label={label} hint={hint} empty={empty} className="sm:col-span-2">
      {(control) => (
        <div role="group" aria-label={`${label} rows`} className="flex flex-col gap-2">
          {rows.map((row, index) => (
            <div key={index} className="flex gap-2">
              <input
                {...(index === 0 ? control : {})}
                aria-label={`${label} ${nameLabel} ${index + 1}`}
                className={cn(control.className, "min-w-0 flex-1")}
                value={row.name}
                onChange={(event) => edit(index, { name: event.target.value })}
              />
              <input
                aria-label={`${label} ${valueLabel} ${index + 1}`}
                className={cn(control.className, "min-w-0 flex-1")}
                {...(numeric ? { type: "number", inputMode: "decimal", step: "any" } : {})}
                value={row.value}
                onChange={(event) => edit(index, { value: event.target.value })}
              />
              <Button
                type="button"
                variant="outline"
                className="h-11"
                aria-label={`Remove ${label} row ${index + 1}`}
                onClick={() => set(rows.filter((_, at) => at !== index))}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" className="h-11 self-start" onClick={() => set([...rows, { name: "", value: "" }])}>
            Add row
          </Button>
        </div>
      )}
    </ConfigField>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <h2 id={id} className="text-lg font-medium">
        {title}
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
  )
}
