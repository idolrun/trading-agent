"use client"

import Link from "next/link"
import { QueryState } from "@/components/query-state"
import { CardActions } from "@/features/cards/card-actions"
import { CardSummary } from "@/features/cards/card-summary"
import { useDecideCard } from "@/features/cards/mutations"
import { useCard } from "@/features/cards/queries"

export function CardDetailScreen({ cardId }: { cardId: string }) {
  const query = useCard(cardId)
  const decide = useDecideCard()

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 px-4 py-6 sm:px-6">
      <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
        Inbox
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight">Card</h1>
      {decide.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {decide.error.message}
        </p>
      ) : null}
      <QueryState query={query} loadingLabel="Loading card" emptyTitle="Card not found">
        {(card) => (
          <CardSummary
            card={card}
            linked={false}
            actions={
              <CardActions
                card={card}
                pending={decide.isPending}
                onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
              />
            }
          />
        )}
      </QueryState>
    </main>
  )
}
