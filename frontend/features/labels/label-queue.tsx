"use client"

import { Input } from "@/components/ui/input"

import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { Badge } from "@/components/badge"
import { Button } from "@/components/ui/button"
import { words } from "@/features/cards/types"
import { LabelEditor } from "@/features/labels/label-editor"
import { useLabelProgress, useLabelQueue } from "@/features/labels/queries"
import { keyIgnored } from "@/features/labels/types"
import type { LabelProgress } from "@/features/labels/types"
import { formatTime } from "@/lib/format"


function Counter({ name, value, min }: { name: string; value: number; min: number }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <span>{name} {value} / {min}</span>
      <div role="progressbar" aria-label={name} aria-valuenow={value} aria-valuemin={0} aria-valuemax={min} className="h-2 rounded-md bg-muted">
        <div className="h-2 rounded-md bg-foreground" style={{ width: `${Math.min(100, (value / Math.max(min, 1)) * 100)}%` }} />
      </div>
    </div>
  )
}

function Progress({ data }: { data: LabelProgress }) {
  return (
    <section aria-label="Progress toward the replay gate" className="grid gap-3 sm:grid-cols-3">
      <Counter name="actionable" value={data.actionable} min={data.minActionable} />
      <Counter name="non-actionable" value={data.nonActionable} min={data.minNonActionable} />
      <Counter name="providers" value={data.providers} min={data.minProviders} />
    </section>
  )
}

export function LabelQueue() {
  const [providerText, setProviderText] = useState("")
  const [providerId, setProviderId] = useState<string>()
  const [selected, setSelected] = useState<string>()
  const query = useLabelQueue({ providerId })
  const progress = useLabelProgress()
  const invalid = providerText.trim() !== "" && !/^\d+$/.test(providerText.trim())

  const items = query.data?.items ?? []
  const index = Math.max(0, items.findIndex((item) => item.eventKey === selected))
  const current = items[index]

  // j / k move through the queue; the editor owns the labeling keys.
  const move = useRef<(by: number) => void>(() => {})
  useEffect(() => {
    move.current = (by) => {
      const next = items[index + by]
      if (next) setSelected(next.eventKey)
    }
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (keyIgnored(event)) return
      const key = event.key.toLowerCase()
      if (key === "j") move.current(1)
      else if (key === "k") move.current(-1)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  // The saved item drops out of the queue on refetch; land on its neighbour.
  const advance = () => setSelected((items[index + 1] ?? items[index - 1])?.eventKey)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (invalid) return
    setProviderId(providerText.trim() || undefined)
    setSelected(undefined)
  }

  return (
    <div className="flex flex-col gap-4">
      {progress.data ? <Progress data={progress.data} /> : null}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Provider ID
          <Input value={providerText} onChange={(e) => setProviderText(e.target.value)} inputMode="numeric" autoComplete="off" aria-invalid={invalid} className="w-44" />
        </label>
        <Button type="submit" variant="outline" className="h-11" disabled={invalid}>Filter</Button>
        {invalid ? <p role="alert" className="w-full text-sm text-destructive">Provider ID is a number.</p> : null}
      </form>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading queue"
        emptyTitle="Queue empty"
        emptyDescription={providerId ? "Nothing left to label for this provider." : "Every message is labeled."}
      >
        {(data) => (
          <>
            {current ? <LabelEditor key={current.eventKey} item={current} onDone={advance} /> : null}
            <ul aria-label="Queue" className="flex max-h-72 flex-col gap-1 overflow-y-auto">
              {data.items.map((item) => (
                <li key={item.eventKey}>
                  <button
                    type="button"
                    aria-current={item.eventKey === current?.eventKey ? "true" : undefined}
                    onClick={() => setSelected(item.eventKey)}
                    className="flex min-h-11 w-full flex-col items-start rounded-md border border-border px-3 py-2 text-left text-sm hover:bg-muted aria-[current=true]:border-foreground aria-[current=true]:bg-muted"
                  >
                    <span className="flex w-full flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      Provider {item.providerId} · {formatTime(item.postedAt)}
                      <Badge>{item.prelabel ? (item.prelabel.signals.map((s) => words(s.msgType)).join(", ") || "No signal") : "No prelabel"}</Badge>
                    </span>
                    <span className="line-clamp-2 break-words">{item.text}</span>
                  </button>
                </li>
              ))}
            </ul>
            <LoadMore query={query} />
          </>
        )}
      </QueryState>
    </div>
  )
}
