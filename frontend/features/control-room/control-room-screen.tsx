"use client"

import Link from "next/link"
import { useMemo, useState } from "react"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { deskFrame, deskTitle } from "@/components/screen"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { decisionStateFor, generalDecisionError, useDecideCard } from "@/features/cards/mutations"
import { useCardList } from "@/features/cards/queries"
import { isDecidable, type Card } from "@/features/cards/types"
import { CompactCard } from "@/features/control-room/compact-card"
import { useDiscordStatus } from "@/features/discord/queries"
import { useFeed } from "@/features/feed/queries"
import { feedItemKey, type FeedItem } from "@/features/feed/types"
import { formatStamp, providerLabel } from "@/lib/format"

const PENDING = { state: "pending" } as const
const GRID = "grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"

type Decide = ReturnType<typeof useDecideCard>

/**
 * Lists come newest first. Items above the first one the user has already seen are new: they wait
 * behind the pill instead of pushing the list down. Older pages (Load more) and removals pass through.
 * `reset` means nothing seen is left (first load, or every seen item was decided): take a new snapshot.
 */
export function splitHeld<T>(items: T[], seen: ReadonlySet<string> | null, key: (item: T) => string) {
  const index = seen == null ? -1 : items.findIndex((item) => seen.has(key(item)))
  if (index < 0) return { visible: items, fresh: 0, reset: items.length > 0 }
  return { visible: items.slice(index), fresh: index, reset: false }
}

function useHeld<T>(items: T[], key: (item: T) => string) {
  const [seen, setSeen] = useState<ReadonlySet<string> | null>(null)
  const split = splitHeld(items, seen, key)
  const release = () => setSeen(new Set(items.map(key)))
  if (split.reset) release()
  return { visible: split.visible, fresh: split.fresh, release }
}

function NewPill({ count, noun, onShow }: { count: number; noun: string; onShow: () => void }) {
  return (
    <>
      <p role="status" aria-atomic="true" className="sr-only">
        {count > 0 ? `${count} new ${noun} waiting` : ""}
      </p>
      {count > 0 ? (
        <div className="sticky top-[calc(var(--desk-header-height,3.5rem)+0.5rem)] z-10 flex justify-center">
          <Button type="button" size="sm" className="shadow-md" onClick={onShow}>
            {count} new — show
          </Button>
        </div>
      ) : null}
    </>
  )
}

function Decidable({ card, decide }: { card: Card; decide: Decide }) {
  const state = decisionStateFor(decide, card.id)
  return (
    <CompactCard
      card={card}
      pending={state.pending}
      blocked={state.blocked}
      error={state.fieldError}
      onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
    />
  )
}

function ActionView({ cards, decide }: { cards: Card[]; decide: Decide }) {
  const { visible, fresh, release } = useHeld(cards, (card) => card.id)
  return (
    <div className="flex flex-col gap-2">
      <NewPill count={fresh} noun={fresh === 1 ? "card" : "cards"} onShow={release} />
      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing waiting on a decision.</p>
      ) : (
        <ul className={GRID}>
          {visible.map((card) => (
            <li key={card.id}>
              <Decidable card={card} decide={decide} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Message({
  item,
  channel,
  cards,
  decide,
}: {
  item: FeedItem
  channel: string
  cards: Map<string, Card>
  decide: Decide
}) {
  const open = item.cardIds.map((id) => cards.get(id)).filter((card) => card != null)
  const other = item.cardIds.filter((id) => !cards.has(id))
  return (
    <div data-message-id={item.messageId} className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex min-w-0 items-baseline gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 truncate">
          <span className="font-medium text-foreground">{providerLabel(item.providerId, open[0]?.providerName)}</span>
          {" · "}
          {channel}
        </span>
        <time dateTime={item.postedAt} className="ml-auto shrink-0 tabular-nums">
          {formatStamp(item.postedAt)}
        </time>
      </div>
      <p className="line-clamp-3 text-sm [overflow-wrap:anywhere] whitespace-pre-wrap">
        {item.text || <span className="text-muted-foreground italic">(no text)</span>}
      </p>
      {open.map((card) => (
        <Decidable key={card.id} card={card} decide={decide} />
      ))}
      {other.length > 0 ? (
        <p className="flex flex-wrap gap-x-3 text-xs">
          {other.map((id) => (
            <Link key={id} href={`/cards/${id}`} className="underline underline-offset-2">
              Card {id.slice(0, 8)}
            </Link>
          ))}
        </p>
      ) : null}
    </div>
  )
}

function MessagesView({ cards, decide }: { cards: Map<string, Card>; decide: Decide }) {
  const query = useFeed({})
  const discord = useDiscordStatus()
  const { visible, fresh, release } = useHeld(query.data?.items ?? [], feedItemKey)
  const names = useMemo(
    () => new Map((discord.data?.channels ?? []).map((channel) => [channel.id, channel.name])),
    [discord.data],
  )
  const channel = (id: string) => names.get(id) || (id.length > 6 ? `Channel …${id.slice(-4)}` : `Channel ${id}`)
  return (
    <QueryState
      query={query}
      isEmpty={(data) => data.items.length === 0}
      loadingLabel="Loading messages"
      emptyTitle="No messages yet"
      emptyLayout="line"
    >
      {() => (
        <div className="flex flex-col gap-2">
          <NewPill count={fresh} noun={fresh === 1 ? "message" : "messages"} onShow={release} />
          <ul className={GRID}>
            {visible.map((item) => (
              <li key={feedItemKey(item)}>
                <Message item={item} channel={channel(item.channelId)} cards={cards} decide={decide} />
              </li>
            ))}
          </ul>
          <LoadMore query={query} />
        </div>
      )}
    </QueryState>
  )
}

/** Triage at volume: small cards, a frozen list, new items only when asked for. */
export function ControlRoomScreen() {
  const pending = useCardList(PENDING)
  const decide = useDecideCard()
  const failure = generalDecisionError(decide)
  const [tab, setTab] = useState("action")
  const cards = useMemo(() => (pending.data?.items ?? []).filter(isDecidable), [pending.data])
  const byId = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards])

  return (
    <div className={deskFrame}>
      <Tabs value={tab} onValueChange={setTab} className="gap-3">
        <header className="flex flex-wrap items-center gap-3">
          <h1 className={deskTitle}>Control room</h1>
          <TabsList>
            <TabsTrigger value="action">Needs action · {cards.length}</TabsTrigger>
            <TabsTrigger value="all">All messages</TabsTrigger>
          </TabsList>
          <Button type="button" size="sm" variant="ghost" className="ml-auto" onClick={() => void pending.refetch()}>
            Refresh
          </Button>
        </header>
        {failure ? (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        ) : null}
        <TabsContent value="action" className="flex flex-col gap-2">
          <QueryState query={pending} loadingLabel="Loading cards" emptyTitle="Nothing waiting" emptyLayout="line">
            {() => (
              <>
                <ActionView cards={cards} decide={decide} />
                <LoadMore query={pending} />
              </>
            )}
          </QueryState>
        </TabsContent>
        <TabsContent value="all">
          <MessagesView cards={byId} decide={decide} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
