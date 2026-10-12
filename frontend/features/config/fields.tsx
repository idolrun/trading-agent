"use client"

import { createContext, useContext, useId, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { HelpTip } from "@/features/config/help-tip"
import { snowflakeError } from "@/features/config/validate"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { INT_FIELDS, type Draft, type ListKey, type RowsKey, type ScalarKey, type ServerErrors } from "@/features/config/types"
import { cn } from "@/lib/utils"

type FormState = {
  draft: Draft
  patch: (changes: Partial<Draft>) => void
  /** Top-level keys the server says are unset (entries disabled), as of the last load/save. */
  missing: ReadonlySet<string>
  errors: ServerErrors
  /** Client-side problems by control name (`validateDraft`); the server still has the last word. */
  problems: Record<string, string>
}

const FormContext = createContext<FormState | null>(null)
export const FormProvider = FormContext.Provider

export function useConfigForm(): FormState {
  const state = useContext(FormContext)
  if (!state) throw new Error("useConfigForm outside FormProvider")
  return state
}

export const UNSET_HINT = "not set: entries disabled"

/** Messages for one control: client check first, then whatever the server said (422). */
function useFieldMessages(name: string): string[] {
  const { errors, problems } = useConfigForm()
  return [...(problems[name] ? [problems[name]] : []), ...(errors.fields[name] ?? [])]
}

/** Per-field errors, announced to screen readers and linked from the control by `id`. */
export function FieldErrors({ name, id }: { name: string; id?: string }) {
  const messages = useFieldMessages(name)
  if (messages.length === 0) return null
  return (
    <div id={id} className="flex flex-col gap-0.5">
      {messages.map((message) => (
        <p key={message} role="alert" className="text-xs text-destructive">
          {message}
        </p>
      ))}
    </div>
  )
}

type Control = {
  id: string
  className: string
  "aria-describedby"?: string
  "aria-invalid"?: true
  "data-unset"?: true
}

/**
 * Label + help + control + hint. A field the server lists as unset, and that is still empty,
 * gets a warning border and the "not set: entries disabled" hint.
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
  const { missing } = useConfigForm()
  const unset = empty && missing.has(name)
  const invalid = useFieldMessages(name).length > 0
  const describedBy = [unset && `${id}-unset`, hint && `${id}-hint`, invalid && `${id}-err`].filter(Boolean).join(" ") || undefined
  return (
    <div className={cn("flex flex-col gap-1 text-sm", className)}>
      <div className="flex items-center gap-1.5">
        <Label htmlFor={id} className="leading-normal select-text">
          {label}
        </Label>
        <HelpTip name={name} label={label} />
      </div>
      {children({
        id,
        className: cn("scroll-mb-28", invalid && "border-destructive", unset && "border-warning bg-warning/5"),
        "aria-describedby": describedBy,
        ...(invalid ? { "aria-invalid": true as const } : {}),
        ...(unset ? { "data-unset": true as const } : {}),
      })}
      {unset ? (
        <p id={`${id}-unset`} className="text-xs font-medium text-warning-foreground">
          {UNSET_HINT}
        </p>
      ) : null}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      <FieldErrors name={name} id={`${id}-err`} />
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
        <Input
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

/** A true on/off setting. `name` is the help key. */
export function CheckField({
  name,
  label,
  checked,
  onChange,
}: {
  name: string
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const id = useId()
  return (
    <div className="flex min-h-11 items-center gap-2 text-sm">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id} className="leading-normal select-text">
        {label}
      </Label>
      <HelpTip name={name} label={label} />
    </div>
  )
}

/** A list of ids (snowflake strings): add one at a time, remove any. */
export function ListField({ name, label }: { name: ListKey; label: string }) {
  const { draft, patch } = useConfigForm()
  const [text, setText] = useState("")
  const [problem, setProblem] = useState<string | null>(null)
  const items = draft[name]
  const add = () => {
    const value = text.trim()
    if (!value) return
    const bad = snowflakeError(value) ?? (items.includes(value) ? `${value} is already in the list.` : null)
    setProblem(bad)
    if (bad) return
    patch({ [name]: [...items, value] })
    setText("")
  }
  return (
    <ConfigField name={name} label={label} className="sm:col-span-2">
      {(control) => (
        <>
          {items.length > 0 ? (
            <ul aria-label={`${label} list`} className="flex flex-wrap gap-2">
              {items.map((item) => (
                <li key={item} className="flex items-center gap-1 rounded-md border border-border bg-muted py-0.5 pr-1 pl-3 text-xs tabular-nums">
                  {item}
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${item} from ${label}`}
                    className="font-normal text-xs hover:bg-background"
                    onClick={() => patch({ [name]: items.filter((other) => other !== item) })}
                  >
                    ×
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="flex gap-2">
            <Input
              {...control}
              className={cn(control.className, "flex-1")}
              inputMode="numeric"
              placeholder="e.g. 123456789012345678"
              aria-invalid={problem ? true : undefined}
              value={text}
              onChange={(event) => {
                setText(event.target.value)
                setProblem(null)
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault()
                  add()
                }
              }}
            />
            <Button type="button" variant="outline" onClick={add}>
              Add
            </Button>
          </div>
          {problem ? (
            <p role="alert" className="text-xs text-destructive">
              {problem}
            </p>
          ) : null}
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
              <Input
                {...(index === 0 ? control : {})}
                aria-label={`${label} ${nameLabel} ${index + 1}`}
                className={cn(control.className, "min-w-0 flex-1")}
                value={row.name}
                onChange={(event) => edit(index, { name: event.target.value })}
              />
              <Input
                aria-label={`${label} ${valueLabel} ${index + 1}`}
                className={cn(control.className, "min-w-0 flex-1")}
                {...(numeric ? { type: "number" as const, inputMode: "decimal" as const, step: "any" } : {})}
                value={row.value}
                onChange={(event) => edit(index, { value: event.target.value })}
              />
              <Button
                type="button"
                variant="outline"
                aria-label={`Remove ${label} row ${index + 1}`}
                onClick={() => set(rows.filter((_, at) => at !== index))}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" className="self-start" onClick={() => set([...rows, { name: "", value: "" }])}>
            Add row
          </Button>
        </div>
      )}
    </ConfigField>
  )
}

/** One settings group. `critical` marks trading-safety controls with a visible accent. */
export function Section({
  title,
  description,
  critical = false,
  children,
}: {
  title: string
  description?: string
  critical?: boolean
  children: ReactNode
}) {
  const id = useId()
  return (
    <Card size="sm" aria-labelledby={id} className={cn(critical && "border-l-4 border-l-warning")}>
      <CardHeader>
        <CardTitle>
          <h3 id={id} className="flex items-center gap-2">
            {title}
            {critical ? <span className="rounded bg-warning/15 px-1.5 py-0.5 text-xs font-medium">Safety control</span> : null}
          </h3>
        </CardTitle>
        {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      </CardHeader>
      <CardContent>
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">{children}</div>
      </CardContent>
    </Card>
  )
}
