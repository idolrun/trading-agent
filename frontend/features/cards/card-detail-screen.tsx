"use client"

import Link from "next/link"
import { QueryState } from "@/components/query-state"
import { CardActions } from "@/features/cards/card-actions"
import { CardReview } from "@/features/cards/card-review"
import { decisionStateFor, generalDecisionError, useDecideCard } from "@/features/cards/mutations"
import { useCard, useCardOrder } from "@/features/cards/queries"
import type { CardDetail } from "@/features/cards/types"

function CardWithOrder({
  card,
  decide,
  state,
}: {
  card: CardDetail
  decide: ReturnType<typeof useDecideCard>
  state: ReturnType<typeof decisionStateFor>
}) {
  const order = useCardOrder(card)
  return (
    <CardReview
      card={card}
      order={order.data}
      actions={
        <CardActions
          card={card}
          pending={state.pending}
          error={state.fieldError}
          onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
        />
      }
    />
  )
}

export function CardDetailScreen({ cardId }: { cardId: string }) {
  const query = useCard(cardId)
  const decide = useDecideCard()
  const failure = generalDecisionError(decide)
  const state = decisionStateFor(decide, cardId)

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-4 sm:px-6">
      <Link href="/" className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground">
        Inbox
      </Link>
      {failure ? (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      ) : null}
      <QueryState query={query} loadingLabel="Loading card" emptyTitle="Card not found" emptyLayout="line">
        {(card) => <CardWithOrder card={card} decide={decide} state={state} />}
      </QueryState>
    </div>
  )
}
