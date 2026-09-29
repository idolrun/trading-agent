import { useMutation, useQueryClient } from "@tanstack/react-query"
import { apiPost } from "@/lib/api/client"
import { ApiError } from "@/lib/api/errors"
import { cardKeys } from "@/features/cards/queries"
import type { CardDecision, CardDetail } from "@/features/cards/types"
import { orderKeys } from "@/features/orders/queries"

export type DecideCardVariables = {
  cardId: string
  decision: CardDecision
}

function postCardDecision(cardId: string, decision: CardDecision): Promise<CardDetail> {
  return apiPost<CardDetail>(`/api/v1/cards/${encodeURIComponent(cardId)}/decision`, decision)
}

/**
 * The server decides what a decision does (state, orders, validity), so the
 * client never predicts it. "Deciding" is the mutation's own pending state
 * (`isPending` + `variables.cardId`); caches change only from the response.
 */
export function useDecideCard() {
  const queryClient = useQueryClient()

  return useMutation<CardDetail, ApiError, DecideCardVariables>({
    mutationKey: [...cardKeys.all, "decision"],
    mutationFn: ({ cardId, decision }) => postCardDecision(cardId, decision),
    onMutate: () => queryClient.cancelQueries({ queryKey: cardKeys.all }),
    onSuccess: (card) => {
      queryClient.setQueryData(cardKeys.detail(card.id), card)
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
