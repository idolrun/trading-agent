"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import type { FormEvent } from "react"
import { deskFrame, deskTitle } from "@/components/screen"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { labelBadge } from "@/features/cards/labels"
import { useDiscordStatus } from "@/features/discord/queries"
import { emptyFeedView, matchesFeed, type FeedView } from "@/features/feed/filter"
import { FeedItem } from "@/features/feed/feed-item"
import { feedStatusLabel, feedTypeLabel } from "@/features/feed/present"
import { useFeed } from "@/features/feed/queries"
import { FEED_LABELS, feedItemKey } from "@/features/feed/types"
import type { FeedFilters, FeedItem as Item, FeedLabel } from "@/features/feed/types"

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

function channelLabel(id: string, name?: string | null): string {
  if (name) return name
  return id.length > 6 ? `…${id.slice(-4)}` : id
}

function Filters({
  server,
  onServer,
  view,
  onView,
  items,
  channelNames,
}: {
  server: FeedFilters
  onServer: (next: FeedFilters) => void
  view: FeedView
  onView: (next: FeedView) => void
  items: Item[]
  channelNames: Map<string, string | null>
}) {
  const [provider, setProvider] = useState(server.providerId ?? "")
  const invalid = provider.trim() !== "" && !/^\d+$/.test(provider.trim())

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!invalid) onServer({ ...server, providerId: provider.trim() || undefined })
  }

  const channels = [...new Set(items.map((item) => item.channelId))]
  const statuses = [...new Set(items.map((item) => item.status ?? "none"))]
  const types = [...new Set(items.flatMap((item) => item.msgTypes))]

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs text-muted-foreground sm:max-w-xs">
          Message
          <Input
            value={view.text}
            onChange={(event) => onView({ ...view, text: event.target.value })}
            className="h-9"
            autoComplete="off"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Provider ID
          <Input
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            inputMode="numeric"
            autoComplete="off"
            aria-invalid={invalid}
            className="h-9 w-44"
          />
        </label>
        <Button type="submit" variant="outline" className="h-9" disabled={invalid}>
          Filter
        </Button>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Label
          <NativeSelect
            value={server.label ?? ""}
            onChange={(event) =>
              onServer({ ...server, label: (event.target.value || undefined) as FeedLabel | undefined })
            }
          >
            <NativeSelectOption value="">All</NativeSelectOption>
            {FEED_LABELS.map((label) => (
              <NativeSelectOption key={label} value={label}>
                {labelBadge(label)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Channel
          <NativeSelect value={view.channelId} onChange={(event) => onView({ ...view, channelId: event.target.value })}>
            <NativeSelectOption value="">All loaded</NativeSelectOption>
            {channels.map((id) => (
              <NativeSelectOption key={id} value={id}>
                {channelLabel(id, channelNames.get(id))}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Classification
          <NativeSelect
            value={view.classification}
            onChange={(event) => onView({ ...view, classification: event.target.value })}
          >
            <NativeSelectOption value="">All loaded</NativeSelectOption>
            {statuses.map((status) => (
              <NativeSelectOption key={`status:${status}`} value={`status:${status}`}>
                {status === "none" ? "Not queued" : feedStatusLabel(status)}
              </NativeSelectOption>
            ))}
            {types.map((type) => (
              <NativeSelectOption key={`type:${type}`} value={`type:${type}`}>
                {feedTypeLabel(type)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        <Label className="min-h-9 font-normal leading-normal select-text">
          <Checkbox checked={view.problems} onCheckedChange={(value) => onView({ ...view, problems: value === true })} />
          Stale, late, or failed
        </Label>
        <Label className="min-h-9 font-normal leading-normal select-text">
          <Checkbox checked={view.hasCard} onCheckedChange={(value) => onView({ ...view, hasCard: value === true })} />
          Has a card
        </Label>
      </div>
      {invalid ? (
        <p role="alert" className="text-sm text-destructive">
          Provider ID is a number.
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">
        Provider and label are filtered on the server. Message, channel, classification, and card filters apply to
        loaded messages only.
      </p>
    </form>
  )
}

export function FeedScreen() {
  const [filters, setFilters] = useState<FeedFilters>({})
  const [view, setView] = useState<FeedView>(emptyFeedView)
  const query = useFeed(filters)
  const discord = useDiscordStatus()
  const { away: scrolled, sentinel } = useScrolledAway()
  const channelNames = useMemo(() => {
    const names = new Map<string, string | null>()
    for (const channel of discord.data?.channels ?? []) names.set(channel.id, channel.name)
    return names
  }, [discord.data])

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
      </header>
      <Filters
        server={filters}
        onServer={(next) => {
          setBaseline(null)
          setFilters(next)
        }}
        view={view}
        onView={setView}
        items={items}
        channelNames={channelNames}
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
        emptyLayout="line"
        emptyDescription={
          filters.providerId || filters.label
            ? "No messages match this filter."
            : "Messages show up here as soon as a provider posts."
        }
      >
        {(data) => {
          const visible = data.items.slice(hidden).filter((item) => matchesFeed(item, view))
          return (
            <>
              {visible.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No loaded messages match these filters.
                  {query.hasNextPage ? " Load more to keep looking." : ""}
                </p>
              ) : (
                <ul className="flex flex-col">
                  {visible.map((item) => (
                    <li key={feedItemKey(item)}>
                      <FeedItem item={item} channelName={channelNames.get(item.channelId)} />
                    </li>
                  ))}
                </ul>
              )}
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
          )
        }}
      </QueryState>
    </div>
  )
}
