"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { QueryState } from "@/components/query-state"
import { CardActions } from "@/features/cards/card-actions"
import { CardSummary } from "@/features/cards/card-summary"
import { useDecideCard } from "@/features/cards/mutations"
import { useCardList } from "@/features/cards/queries"
import type { CardState } from "@/features/cards/types"

const VIEWS = [
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "all", label: "All" },
] as const

type CardView = (typeof VIEWS)[number]["id"]

export function CardInbox() {
  const [view, setView] = useState<CardView>("pending")
  const filters = view === "all" ? {} : { state: view satisfies CardState }
  const query = useCardList(filters)
  const decide = useDecideCard()

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-lg font-medium">Cards</h2>
        <div role="group" aria-label="Card state" className="flex flex-wrap gap-2">
          {VIEWS.map((item) => (
            <Button
              key={item.id}
              type="button"
              size="sm"
              variant={item.id === view ? "default" : "outline"}
              aria-pressed={item.id === view}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </Button>
          ))}
          <Button type="button" size="sm" variant="ghost" onClick={() => void query.refetch()}>
            Refresh
          </Button>
        </div>
      </div>
      {decide.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {decide.error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading cards"
        emptyTitle="No cards in this view"
        emptyDescription="Nothing matches this filter."
      >
        {(data) => (
          <ul className="flex flex-col gap-3">
            {data.items.map((card) => (
              <li key={card.id}>
                <CardSummary
                  card={card}
                  actions={
                    <CardActions
                      card={card}
                      pending={decide.isPending && decide.variables?.cardId === card.id}
                      onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
                    />
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </section>
  )
}
