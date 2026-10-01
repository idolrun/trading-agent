"use client"

import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"

import { Input } from "@/components/ui/input"

import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { deskFrame, deskTitle } from "@/components/screen"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { labelBadge } from "@/features/cards/labels"
import { FeedItem } from "@/features/feed/feed-item"
import { useFeed } from "@/features/feed/queries"
import { FEED_LABELS, feedItemKey } from "@/features/feed/types"
import type { FeedFilters, FeedLabel } from "@/features/feed/types"


function useScrolledAway() {
  const [away, setAway] = useState(false)
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = sentinel.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => setAway(!entry.isIntersecting), { threshold: 0 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return { away, sentinel }
}

function Filters({ value, onChange }: { value: FeedFilters; onChange: (next: FeedFilters) => void }) {
  const [provider, setProvider] = useState(value.providerId ?? "")
  const invalid = provider.trim() !== "" && !/^\d+$/.test(provider.trim())

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!invalid) onChange({ ...value, providerId: provider.trim() || undefined })
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm">
        Provider ID
        <Input
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          inputMode="numeric"
          autoComplete="off"
          aria-invalid={invalid}
          className="w-44"
        />
      </label>
      <Button type="submit" variant="outline" className="h-9" disabled={invalid}>
        Filter
      </Button>
      <label className="flex flex-col gap-1 text-sm">
        Label
        <NativeSelect
          value={value.label ?? ""}
          onChange={(event) => onChange({ ...value, label: (event.target.value || undefined) as FeedLabel | undefined })}
         
        >
          <NativeSelectOption value="">All</NativeSelectOption>
          {FEED_LABELS.map((label) => (
            <NativeSelectOption key={label} value={label}>
              {labelBadge(label)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </label>
      {invalid ? (
        <p role="alert" className="w-full text-sm text-destructive">
          Provider ID is a number.
        </p>
      ) : null}
    </form>
  )
}

export function FeedScreen() {
  const [filters, setFilters] = useState<FeedFilters>({})
  const query = useFeed(filters)
  const { away: scrolled, sentinel } = useScrolledAway()

  // Hold the list steady while the user reads: rows above `baseline` are new and stay behind the
  // "New messages" pill. Following the newest row is automatic while at the top of a first page.
  const [baseline, setBaseline] = useState<string | null>(null)
  const items = query.data?.items ?? []
  const top = items[0] ? feedItemKey(items[0]) : null
  const holding = scrolled || (query.data?.pageCount ?? 0) > 1
  if (!holding && baseline !== top) setBaseline(top)
  const found = baseline ? items.findIndex((item) => feedItemKey(item) === baseline) : -1
  const hidden = holding && found > 0 ? found : 0

  const showNew = () => {
    setBaseline(top)
    const reduce =
      typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" })
    void query.refetch()
  }

  return (
    <div className={`relative ${deskFrame}`}>
      <div ref={sentinel} aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-20" />
      <header>
        <h1 className={deskTitle}>Feed</h1>
        <p className="text-sm text-muted-foreground">
          Every message event, with how it was handled. Nothing is filtered unless you choose.
        </p>
      </header>
      <Filters
        value={filters}
        onChange={(next) => {
          setBaseline(null)
          setFilters(next)
        }}
      />
      {hidden > 0 ? (
        <div className="sticky top-[calc(var(--desk-header-height,3.5rem)+0.5rem)] z-10 flex justify-center">
          <Button type="button" size="sm" onClick={showNew} className="shadow-md">
            New messages
          </Button>
        </div>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading messages"
        emptyTitle="No messages"
        emptyDescription={
          filters.providerId || filters.label
            ? "No messages match this filter."
            : "Messages show up here as soon as a provider posts."
        }
      >
        {(data) => (
          <>
            <ul className="flex flex-col gap-3">
              {data.items.slice(hidden).map((item) => (
                <li key={feedItemKey(item)}>
                  <FeedItem item={item} />
                </li>
              ))}
            </ul>
            {query.hasNextPage ? (
              <Button
                type="button"
                variant="outline"
                className="h-11"
                disabled={query.isFetchingNextPage}
                onClick={() => void query.fetchNextPage()}
              >
                {query.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            ) : null}
          </>
        )}
      </QueryState>
    </div>
  )
}
