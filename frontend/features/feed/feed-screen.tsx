"use client"

import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { labelBadge } from "@/features/cards/labels"
import { FeedItem } from "@/features/feed/feed-item"
import { useFeed } from "@/features/feed/queries"
import { FEED_LABELS, feedItemKey } from "@/features/feed/types"
import type { FeedFilters, FeedLabel } from "@/features/feed/types"

const AWAY_PX = 80
const FIELD =
  "h-9 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

function useScrolledAway() {
  const [away, setAway] = useState(false)
  useEffect(() => {
    const onScroll = () => setAway(window.scrollY > AWAY_PX)
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])
  return away
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
        <input
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          inputMode="numeric"
          autoComplete="off"
          aria-invalid={invalid}
          className={`${FIELD} w-44`}
        />
      </label>
      <Button type="submit" variant="outline" className="h-9" disabled={invalid}>
        Filter
      </Button>
      <label className="flex flex-col gap-1 text-sm">
        Label
        <select
          value={value.label ?? ""}
          onChange={(event) => onChange({ ...value, label: (event.target.value || undefined) as FeedLabel | undefined })}
          className={FIELD}
        >
          <option value="">All</option>
          {FEED_LABELS.map((label) => (
            <option key={label} value={label}>
              {labelBadge(label)}
            </option>
          ))}
        </select>
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
  const scrolled = useScrolledAway()

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
    window.scrollTo({ top: 0, behavior: "smooth" })
    void query.refetch()
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6 sm:px-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Feed</h1>
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
        <div className="sticky top-2 z-10 flex justify-center">
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
    </main>
  )
}
