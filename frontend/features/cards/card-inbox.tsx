"use client"

import type { UseInfiniteQueryResult } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import Link from "next/link"
import { useEffect, useMemo, useState } from "react"
import { Badge } from "@/components/badge"
import { LoadMore } from "@/components/load-more"
import { freshnessLabel, freshnessOf, QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { CardActions } from "@/features/cards/card-actions"
import { cardStateLabel, side, TONE_CLASS } from "@/features/cards/card-state"
import { CardSummary } from "@/features/cards/card-summary"
import { cardGroup, isPastDeadline, type CardGroup } from "@/features/cards/groups"
import { decisionStateFor, generalDecisionError, useDecideCard } from "@/features/cards/mutations"
import { useCardList } from "@/features/cards/queries"
import type { Card } from "@/features/cards/types"
import { formatContract, formatStamp, providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

const PENDING = { state: "pending" } as const
// A disabled card has its own state, not "pending" (C75): it is listed apart, with its reason.
const DISABLED = { state: "disabled" } as const
// Everything else (approved, rejected, expired...) is history; the list has no "not pending" filter.
const RECENT = {} as const
const HISTORY: ReadonlySet<string> = new Set(["pending", "disabled"])

type Decide = ReturnType<typeof useDecideCard>
type Cards = UseInfiniteQueryResult<{ items: Card[] }, Error>

/** Re-evaluated every few seconds so a card whose window ends moves to Blocked / stale by itself. */
function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

function CardList({ items, decide }: { items: Card[]; decide: Decide }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {items.map((card) => {
        const state = decisionStateFor(decide, card.id)
        return (
          <li key={card.id}>
            <CardSummary
              card={card}
              actions={
                <CardActions
                  card={card}
                  pending={state.pending}
                  blocked={state.blocked}
                  error={state.fieldError}
                  onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
                />
              }
            />
          </li>
        )
      })}
    </ul>
  )
}

function Heading({ id, title, count, hint }: { id: string; title: string; count?: number; hint?: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <h3 id={id} className="text-sm font-semibold tracking-wide uppercase">
        {title}
      </h3>
      {count == null ? null : (
        <span className="rounded-md bg-muted px-1.5 text-xs font-medium tabular-nums" aria-label={`${count} ${count === 1 ? "card" : "cards"}`}>
          {count}
        </span>
      )}
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  )
}

function Section({
  id,
  title,
  hint,
  items,
  decide,
  emptyText,
}: {
  id: string
  title: string
  hint: string
  items: Card[]
  decide: Decide
  emptyText?: string
}) {
  if (items.length === 0 && !emptyText) return null
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <Heading id={id} title={title} count={items.length} hint={hint} />
      {items.length === 0 ? <p className="text-sm text-muted-foreground">{emptyText}</p> : <CardList items={items} decide={decide} />}
    </section>
  )
}

/** Blocked / stale cards collapse once there are many; the count and the reasons stay in the header. */
function BlockedSection({ items, decide }: { items: Card[]; decide: Decide }) {
  const [open, setOpen] = useState(items.length <= 3)
  if (items.length === 0) return null
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="flex flex-col gap-2">
      <CollapsibleTrigger className="group/blocked flex min-h-9 items-baseline gap-2 text-left" aria-label={`Blocked or stale, ${items.length} ${items.length === 1 ? "card" : "cards"}`}>
        <Heading id="blocked-heading" title="Blocked / stale" count={items.length} hint="Cannot be approved" />
        <ChevronDown aria-hidden className={cn("size-4 self-center text-muted-foreground transition-transform", open && "rotate-180")} />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <CardList items={items} decide={decide} />
      </CollapsibleContent>
    </Collapsible>
  )
}

/** Compact one-line rows: history is for looking up, not for deciding. Fetched only once opened. */
function RecentRows() {
  const query = useCardList(RECENT)
  const items = (query.data?.items ?? []).filter((card) => !HISTORY.has(card.state))
  if (query.isError && !query.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load processed cards: {query.error.message}
      </p>
    )
  }
  if (!query.data) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (items.length === 0) return <p className="text-sm text-muted-foreground">Nothing processed recently.</p>
  return (
    <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
      {items.map((card) => {
        const action = side(card.action)
        const state = cardStateLabel(card.state)
        return (
          <li key={card.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <Badge className={cn("text-xs font-bold uppercase", TONE_CLASS[action.tone])}>{action.label}</Badge>
            <Link href={`/cards/${card.id}`} className="font-medium underline-offset-2 hover:underline">
              {card.contract ? formatContract(card.contract) : (card.underlying ?? "Unknown")}
            </Link>
            <span className={cn("rounded-md border px-1.5 text-xs", TONE_CLASS[state.tone])}>{state.label}</span>
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">
              {card.providerId ? `${providerLabel(card.providerId, card.providerName)} · ` : ""}
              {formatStamp(card.postedAt)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function RecentSection() {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="flex flex-col gap-2">
      <CollapsibleTrigger className="flex min-h-9 items-baseline gap-2 text-left">
        <Heading id="recent-heading" title="Recently processed" hint="Approved, rejected, expired" />
        <ChevronDown aria-hidden className={cn("size-4 self-center text-muted-foreground transition-transform", open && "rotate-180")} />
      </CollapsibleTrigger>
      <CollapsibleContent>{open ? <RecentRows /> : null}</CollapsibleContent>
    </Collapsible>
  )
}

function Filters({
  symbol,
  onSymbol,
  provider,
  onProvider,
  providers,
}: {
  symbol: string
  onSymbol: (value: string) => void
  provider: string
  onProvider: (value: string) => void
  providers: [string, string][]
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Symbol
        <Input
          value={symbol}
          onChange={(event) => onSymbol(event.target.value)}
          placeholder="SPY"
          className="h-9 w-28 uppercase"
          autoComplete="off"
        />
      </label>
      {providers.length > 1 ? (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Provider
          <NativeSelect value={provider} onChange={(event) => onProvider(event.target.value)}>
            <NativeSelectOption value="">All</NativeSelectOption>
            {providers.map(([id, label]) => (
              <NativeSelectOption key={id} value={id}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
      ) : null}
    </div>
  )
}

function Groups({ pending, disabled, decide }: { pending: Cards; disabled: Cards; decide: Decide }) {
  const now = useNow(5_000)
  const [symbol, setSymbol] = useState("")
  const [provider, setProvider] = useState("")
  const all = useMemo(
    () => [...(pending.data?.items ?? []), ...(disabled.data?.items ?? [])],
    [pending.data, disabled.data],
  )
  const providers = useMemo(() => {
    const byId = new Map<string, string>()
    for (const card of all) if (card.providerId) byId.set(card.providerId, providerLabel(card.providerId, card.providerName))
    return [...byId].sort((a, b) => a[1].localeCompare(b[1]))
  }, [all])

  const matches = all.filter(
    (card) =>
      (symbol.trim() === "" || (card.underlying ?? card.contract?.underlying ?? "").includes(symbol.trim().toUpperCase())) &&
      (provider === "" || card.providerId === provider),
  )
  const grouped: Record<CardGroup, Card[]> = { decide: [], review: [], blocked: [], done: [] }
  for (const card of matches) grouped[cardGroup(card, isPastDeadline(card, now))].push(card)
  const filtering = symbol.trim() !== "" || provider !== ""

  return (
    <div className="flex flex-col gap-5">
      {all.length > 3 || filtering ? (
        <Filters symbol={symbol} onSymbol={setSymbol} provider={provider} onProvider={setProvider} providers={providers} />
      ) : null}
      <Section
        id="decide-heading"
        title="Needs decision"
        hint="Valid, waiting on you"
        items={grouped.decide}
        decide={decide}
        emptyText={filtering ? "No matching cards need a decision." : "Nothing waiting on a decision."}
      />
      <Section
        id="review-heading"
        title="Needs review"
        hint="Fields to confirm before approval"
        items={grouped.review}
        decide={decide}
      />
      <BlockedSection items={grouped.blocked} decide={decide} />
      <LoadMore query={pending} />
      <RecentSection />
    </div>
  )
}

export function CardInbox() {
  const query = useCardList(PENDING)
  const disabled = useCardList(DISABLED)
  const decide = useDecideCard()
  const failure = generalDecisionError(decide)
  const disabledCount = disabled.data?.items.length ?? 0
  const freshness = freshnessOf(query)

  return (
    <section aria-labelledby="cards-heading" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-baseline gap-3">
          <h2 id="cards-heading" className="font-heading text-lg font-semibold tracking-tight">
            Trade cards
          </h2>
          {freshness === "none" || freshness === "fresh" ? null : (
            <p
              role="status"
              aria-live="polite"
              className={cn("text-sm uppercase font-semibold", freshness === "stale" ? "text-yellow-700 dark:text-yellow-400" : "text-muted-foreground")}
            >
              {freshnessLabel[freshness]}
            </p>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            void query.refetch()
            void disabled.refetch()
          }}
        >
          Refresh
        </Button>
      </div>
      {failure ? (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      ) : null}
      {disabled.isError && disabledCount === 0 ? (
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load disabled cards: {disabled.error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        compact
        hideFreshness
        isEmpty={(data) => data.items.length === 0 && disabledCount === 0}
        loadingLabel="Loading cards"
        emptyTitle="No cards waiting"
        emptyDescription="A proposed trade shows up here as soon as a provider posts a signal"
      >
        {() => <Groups pending={query} disabled={disabled} decide={decide} />}
      </QueryState>
    </section>
  )
}


