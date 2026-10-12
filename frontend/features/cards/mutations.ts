import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query"
import { toast } from "@/components/toaster"
import { apiPost } from "@/lib/api/client"
import { ApiError, userFacingMessage } from "@/lib/api/errors"
import { cardKeys } from "@/features/cards/queries"
import type { CardDecision, CardDetail } from "@/features/cards/types"
import { orderKeys } from "@/features/orders/queries"

export type DecideCardVariables = {
  cardId: string
  decision: CardDecision
}

type DecideCard = UseMutationResult<CardDetail, ApiError, DecideCardVariables>

function postCardDecision(cardId: string, decision: CardDecision): Promise<CardDetail> {
  return apiPost<CardDetail>(`/api/v1/cards/${encodeURIComponent(cardId)}/decision`, decision)
}

/**
 * The server decides what a decision does (state, orders, validity), so the
 * client never predicts it. "Deciding" is the mutation's own pending state
 * (`isPending` + `variables.cardId`); caches change only from the response.
 * 409 (changed / expired / already decided): toast, and `onSettled` refetches.
 * Approving a manual entry/add needs a fresh password: `withStepUp` (`useStepUp().withStepUp`,
 * with `<StepUpDialog>` mounted by the caller) confirms it and retries once.
 */
export function useDecideCard(withStepUp: <R>(fn: () => Promise<R>) => () => Promise<R>) {
  const queryClient = useQueryClient()

  return useMutation<CardDetail, ApiError, DecideCardVariables>({
    mutationKey: [...cardKeys.all, "decision"],
    mutationFn: ({ cardId, decision }) => withStepUp(() => postCardDecision(cardId, decision))(),
    onMutate: () => queryClient.cancelQueries({ queryKey: cardKeys.all }),
    onSuccess: (card) => {
      queryClient.setQueryData(cardKeys.detail(card.id), card)
    },
    onError: (error) => {
      if (error.status === 409) toast("Card changed or expired")
    },
    onSettled: (_card, error, variables) => {
      const refreshes = [
        queryClient.invalidateQueries({ queryKey: cardKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: cardKeys.detail(variables.cardId) }),
      ]
      if (!error && variables.decision.decision === "approve") {
        refreshes.push(queryClient.invalidateQueries({ queryKey: orderKeys.lists() }))
      }
      return Promise.all(refreshes)
    },
  })
}

/**
 * One mutation serves every card on screen: this card is "deciding" while its own
 * request is in flight (`blocked` for the others, so requests never overlap), and a 422
 * is the server's message about this card's fields. Other failures are `generalError`.
 */
export function decisionStateFor(decide: DecideCard, cardId: string) {
  const mine = decide.variables?.cardId === cardId
  const failed = decide.isError ? decide.error : null
  return {
    pending: decide.isPending && mine,
    blocked: decide.isPending && !mine,
    fieldError: failed && mine && failed.status === 422 ? failed.message : null,
  }
}

/** A failure that is neither the 409 toast nor a 422 shown at the fields. */
export function generalDecisionError(decide: DecideCard): string | null {
  const error = decide.isError ? decide.error : null
  if (!error || error.status === 409 || error.status === 422) return null
  return userFacingMessage(error, { order: decide.variables?.decision.decision === "approve" })
}
