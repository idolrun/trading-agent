"use client"

import { X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { highlightSpans } from "@/features/cards/source-messages"
import { words } from "@/features/cards/types"
import { useSaveLabel } from "@/features/labels/mutations"
import {
  BOOLEAN_FIELDS, ENUM_FIELDS, FIELD_NAMES, INSTRUMENTS, MSG_TYPES,
  cleanLabel, emptyValue, fieldSpans, keyIgnored, removeSignal,
} from "@/features/labels/types"
import type { LabelLink, LabelSignal, MsgType, QueueItem } from "@/features/labels/types"
import { usePositionList } from "@/features/positions/queries"
import { formatTime, providerLabel } from "@/lib/format"

function FieldInput({ name, value, onChange }: { name: string; value: unknown; onChange: (value: unknown) => void }) {
  if (BOOLEAN_FIELDS.includes(name)) {
    return <Checkbox aria-label={words(name)} checked={value === true} onCheckedChange={(checked) => onChange(checked === true)} />
  }
  const options = ENUM_FIELDS[name]
  if (options) {
    return (
      <NativeSelect aria-label={words(name)} data-field-input value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {options.map((option) => <NativeSelectOption key={option} value={option}>{option}</NativeSelectOption>)}
      </NativeSelect>
    )
  }
  return (
    <Input
      aria-label={words(name)}
      data-field-input
      autoComplete="off"
      value={Array.isArray(value) ? value.join(", ") : String(value ?? "")}
      onChange={(e) => onChange(name === "tp_levels" ? e.target.value.split(",").map((v) => v.trimStart()) : e.target.value)}
    />
  )
}

function SignalEditor({ signal, onChange, onRemove }: { signal: LabelSignal; onChange: (signal: LabelSignal) => void; onRemove: () => void }) {
  const names = Object.keys(signal.fields)
  const remaining = FIELD_NAMES.filter((name) => !names.includes(name))
  const setFields = (fields: Record<string, unknown>) => onChange({ ...signal, fields })
  return (
    <fieldset className="flex flex-col gap-2 rounded-md border border-border px-3 py-2">
      <legend className="px-1 text-sm font-medium">Signal {signal.idx + 1}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect aria-label={`Type ${signal.idx + 1}`} value={signal.msgType} onChange={(e) => onChange({ ...signal, msgType: e.target.value as MsgType })}>
          {MSG_TYPES.map((type) => <NativeSelectOption key={type} value={type}>{words(type)}</NativeSelectOption>)}
        </NativeSelect>
        <NativeSelect aria-label={`Instrument ${signal.idx + 1}`} value={signal.instrument ?? ""} onChange={(e) => onChange({ ...signal, instrument: (e.target.value || null) as LabelSignal["instrument"] })}>
          <NativeSelectOption value="">Instrument: none</NativeSelectOption>
          {INSTRUMENTS.map((instrument) => <NativeSelectOption key={instrument} value={instrument}>{instrument}</NativeSelectOption>)}
        </NativeSelect>
        <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={onRemove}>
          Remove signal {signal.idx + 1}
        </Button>
      </div>
      {names.map((name) => (
        <div key={name} className="flex items-center gap-2 text-sm">
          <span className="w-40 shrink-0">{words(name)}</span>
          <FieldInput name={name} value={signal.fields[name]} onChange={(value) => setFields({ ...signal.fields, [name]: value })} />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label={`Remove ${words(name)}`}
            onClick={() => setFields(Object.fromEntries(Object.entries(signal.fields).filter(([key]) => key !== name)))}
          >
            <X aria-hidden className="size-4" />
          </Button>
        </div>
      ))}
      <NativeSelect aria-label={`Add field ${signal.idx + 1}`} value="" onChange={(e) => e.target.value && setFields({ ...signal.fields, [e.target.value]: emptyValue(e.target.value) })} className="self-start">
        <NativeSelectOption value="">Add field…</NativeSelectOption>
        {remaining.map((name) => <NativeSelectOption key={name} value={name}>{words(name)}</NativeSelectOption>)}
      </NativeSelect>
    </fieldset>
  )
}

/** Only this provider's positions that existed when the message was posted. */
function LinkPicker({ item, link, count, onChange }: { item: QueueItem; link: LabelLink | null; count: number; onChange: (link: LabelLink | null) => void }) {
  const positions = usePositionList({ providerId: item.providerId as unknown as number })
  const posted = new Date(item.postedAt).getTime()
  const candidates = (positions.data?.items ?? []).filter(
    (p) => p.providerId === item.providerId && new Date(p.openedAt).getTime() <= posted,
  )
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-sm font-medium">Link to position</legend>
      {positions.isPending ? <p className="text-sm text-muted-foreground">Loading positions</p> : null}
      {positions.isError ? <p role="alert" className="text-sm text-destructive">{positions.error.message}</p> : null}
      <RadioGroup
        value={link?.providerPositionId ?? "none"}
        onValueChange={(id) => onChange(id === "none" ? null : { idx: link?.idx ?? 0, providerPositionId: id })}
        className="gap-1"
      >
        <Label className="min-h-11 gap-3 font-normal leading-normal select-text">
          <RadioGroupItem value="none" />
          No link
        </Label>
        {candidates.map((p) => (
          <Label key={p.id} className="min-h-11 gap-3 font-normal leading-normal select-text">
            <RadioGroupItem value={p.id} />
            <span>
              {[p.underlying, p.expiry, p.strike, p.optionRight].filter(Boolean).join(" ")}
              <span className="block text-xs text-muted-foreground">{p.status}, opened {formatTime(p.openedAt)}</span>
            </span>
          </Label>
        ))}
      </RadioGroup>
      {link && count > 1 ? (
        <Label className="gap-2 font-normal leading-normal select-text">
          Link belongs to signal
          <NativeSelect value={String(link.idx)} onChange={(e) => onChange({ ...link, idx: Number(e.target.value) })} className="w-auto">
            {Array.from({ length: count }, (_, i) => <NativeSelectOption key={i} value={String(i)}>{i + 1}</NativeSelectOption>)}
          </NativeSelect>
        </Label>
      ) : null}
    </fieldset>
  )
}

/**
 * One queue item. Render with `key={item.eventKey}` so edits never leak between messages.
 * Keys: c / Enter confirm the prelabel, n no signal, e edit the first field.
 */
export function LabelEditor({ item, onDone }: { item: QueueItem; onDone: () => void }) {
  const [signals, setSignals] = useState<LabelSignal[]>(item.prelabel?.signals ?? [])
  const [link, setLink] = useState<LabelLink | null>(item.prelabel?.link ?? null)
  const root = useRef<HTMLDivElement>(null)
  const save = useSaveLabel()

  const submit = (label: ReturnType<typeof cleanLabel>) => {
    if (!save.isPending) save.mutate({ eventKey: item.eventKey, label }, { onSuccess: onDone })
  }
  const confirm = () => item.prelabel && submit(item.prelabel)
  const noSignal = () => submit({ signals: [], link: null })

  const latest = useRef({ confirm, noSignal })
  useEffect(() => {
    latest.current = { confirm, noSignal }
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (keyIgnored(event)) return
      const key = event.key.toLowerCase()
      if (key === "c" || key === "enter") latest.current.confirm()
      else if (key === "n") latest.current.noSignal()
      else if (key === "e") {
        event.preventDefault() // else the "e" lands in the field just focused
        root.current?.querySelector<HTMLElement>("[data-field-input]")?.focus()
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  const parent = item.context.filter((m) => m.isReplyParent)
  const context = [...parent, ...item.context.filter((m) => !m.isReplyParent)]

  return (
    <div ref={root} className="flex flex-col gap-3">
      <section aria-label="Message" className="flex flex-col gap-2">
        <p className="text-xs text-muted-foreground tabular-nums">
          {providerLabel(item.providerId)} · <time dateTime={item.postedAt}>{formatTime(item.postedAt)}</time>
        </p>
        {context.map((m) => (
          <p key={m.id} className="border-l-2 border-border pl-3 text-sm break-words whitespace-pre-wrap text-muted-foreground">
            <span className="block text-xs">{m.isReplyParent ? "Replied to" : "Earlier"} · {m.atEt}</span>
            {m.text}
          </p>
        ))}
        <p data-testid="message-text" className="border-l-2 border-foreground/40 pl-3 text-sm break-words whitespace-pre-wrap">
          {highlightSpans(item.text, fieldSpans(item.text, signals))}
        </p>
      </section>

      {item.prelabel == null ? <p className="text-sm text-muted-foreground">No prelabel for this message.</p> : null}
      {signals.map((signal) => (
        <SignalEditor
          key={signal.idx}
          signal={signal}
          onChange={(next) => setSignals(signals.map((s) => (s.idx === next.idx ? next : s)))}
          onRemove={() => {
            const next = removeSignal(signals, link, signal.idx)
            setSignals(next.signals)
            setLink(next.link)
          }}
        />
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => setSignals([...signals, { idx: signals.length, msgType: "entry", fields: {} }])}
      >
        Add signal
      </Button>
      <LinkPicker item={item} link={link} count={signals.length} onChange={setLink} />

      <div className="flex flex-wrap gap-2">
        <Button type="button" className="h-11" aria-keyshortcuts="C Enter" disabled={item.prelabel == null || save.isPending} onClick={confirm}>
          {save.isPending ? "Saving…" : "Confirm"}
        </Button>
        <Button type="button" variant="outline" className="h-11" aria-keyshortcuts="N" disabled={save.isPending} onClick={noSignal}>
          No signal
        </Button>
        <Button type="button" variant="outline" className="h-11" disabled={save.isPending} onClick={() => submit(cleanLabel(signals, link))}>
          Save correction
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">Keys: C or Enter confirm, N no signal, E edit fields, J / K next / previous.</p>
    </div>
  )
}
