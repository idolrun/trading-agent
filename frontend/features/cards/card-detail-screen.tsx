"use client"

import Link from "next/link"
import { deskFrame, deskTitle } from "@/components/screen"
import { QueryState } from "@/components/query-state"
import { StepUpDialog } from "@/features/auth/step-up-dialog"
import { useStepUp } from "@/features/auth/use-step-up"
import { CardActions } from "@/features/cards/card-actions"
import { CardSummary } from "@/features/cards/card-summary"
import { decisionStateFor, generalDecisionError, useDecideCard } from "@/features/cards/mutations"
import { useCard } from "@/features/cards/queries"

export function CardDetailScreen({ cardId }: { cardId: string }) {
  const query = useCard(cardId)
  const stepUp = useStepUp()
  const decide = useDecideCard(stepUp.withStepUp)
  const failure = generalDecisionError(decide)
  const state = decisionStateFor(decide, cardId)

  return (
    <div className={deskFrame}>
      <Link href="/" className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground">
        Inbox
      </Link>
      <h1 className={deskTitle}>Card</h1>
      {failure ? (
        <p role="alert" className="text-sm text-destructive">
          {failure}
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
                pending={state.pending}
                error={state.fieldError}
                onDecide={(decision) => decide.mutate({ cardId: card.id, decision })}
              />
            }
          />
        )}
      </QueryState>
      <StepUpDialog {...stepUp.dialogProps} />
    </div>
  )
}
