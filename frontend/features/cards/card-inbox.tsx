"use client"

import type { UseInfiniteQueryResult } from "@tanstack/react-query"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { CardActions } from "@/features/cards/card-actions"
import { CardSummary } from "@/features/cards/card-summary"
import { decisionStateFor, generalDecisionError, useDecideCard } from "@/features/cards/mutations"
import { useCardList } from "@/features/cards/queries"
import type { Card } from "@/features/cards/types"

const PENDING = { state: "pending" } as const
// A disabled card has its own state, not "pending" (C75): it is listed apart, with its reason.
const DISABLED = { state: "disabled" } as const

type Decide = ReturnType<typeof useDecideCard>
type Cards = UseInfiniteQueryResult<{ items: Card[] }, Error>

function CardList({ query, items, decide }: { query: Cards; items: Card[]; decide: Decide }) {
  return (
    <>
      <ul className="flex flex-col gap-3">
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
}

function DisabledCards({ decide }: { decide: Decide }) {
  const query = useCardList(DISABLED)
  const items = query.data?.items ?? []
  if (query.isError && items.length === 0) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t load disabled cards: {query.error.message}
      </p>
    )
  }
  if (items.length === 0) return null

  return (
    <section aria-labelledby="disabled-heading" className="flex flex-col gap-3">
      <h3 id="disabled-heading" className="text-base font-medium">
        Disabled
      </h3>
      <CardList query={query} items={items} decide={decide} />
    </section>
  )
}

export function CardInbox() {
  const query = useCardList(PENDING)
  const decide = useDecideCard()
  const failure = generalDecisionError(decide)

  return (
    <section aria-labelledby="cards-heading" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 id="cards-heading" className="text-lg font-medium">
          Cards
        </h2>
        <Button type="button" size="sm" variant="ghost" onClick={() => void query.refetch()}>
          Refresh
        </Button>
      </div>
      {failure ? (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading cards"
        emptyTitle="No cards waiting"
        emptyDescription="A proposed trade shows up here as soon as a provider posts a signal."
      >
        {(data) => <CardList query={query} items={data.items} decide={decide} />}
      </QueryState>
      <DisabledCards decide={decide} />
    </section>
  )
}
