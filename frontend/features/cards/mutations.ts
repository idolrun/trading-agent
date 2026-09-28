import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query"
import { apiPost } from "@/lib/api/client"
import { ApiError } from "@/lib/api/errors"
import { restoreQueries, snapshotQueries, type CacheSnapshot } from "@/lib/query/snapshot"
import { applyCardDecision, cardMatchesFilters } from "@/features/cards/decision"
import { cardKeys, type CardList } from "@/features/cards/queries"
import type { Card, CardDecision, CardListFilters } from "@/features/cards/types"
import { orderKeys } from "@/features/orders/queries"

export type DecideCardVariables = {
  cardId: string
  decision: CardDecision
}

function postCardDecision(cardId: string, decision: CardDecision): Promise<Card> {
  return apiPost<Card>(`/api/v1/cards/${encodeURIComponent(cardId)}/decision`, decision)
}

function sameQueryKey(left: QueryKey, right: QueryKey) {
  return (
    left.length === right.length &&
    left.every((part, index) => Object.is(part, right[index]))
  )
}

function filtersFromListKey(queryKey: QueryKey): CardListFilters {
  const filters = queryKey[2]
  if (!filters || typeof filters !== "object") return {}
  return filters as CardListFilters
}

function findCachedCard(cardId: string, snapshot: CacheSnapshot): Card | undefined {
  for (const [queryKey, data] of snapshot) {
    if (queryKey[1] === "detail" && queryKey[2] === cardId && data) {
      return data as Card
    }
  }
  for (const [, data] of snapshot) {
    if (!data || typeof data !== "object" || !("items" in data)) continue
    const found = (data as CardList).items.find((card) => card.id === cardId)
    if (found) return found
  }
  return undefined
}

function patchCardList(list: CardList, next: Card, filters: CardListFilters): CardList {
  const index = list.items.findIndex((card) => card.id === next.id)
  const matches = cardMatchesFilters(next, filters)
  if (index === -1) {
    return matches ? { items: [next, ...list.items] } : list
  }
  if (!matches) {
    return { items: list.items.filter((card) => card.id !== next.id) }
  }
  const items = list.items.slice()
  items[index] = next
  return { items }
}

function patchCardCaches(
  queryClient: ReturnType<typeof useQueryClient>,
  next: Card,
  writeDetail: boolean,
) {
  if (writeDetail) {
    queryClient.setQueryData<Card>(cardKeys.detail(next.id), next)
  }
  for (const query of queryClient.getQueryCache().findAll({ queryKey: cardKeys.lists() })) {
    queryClient.setQueryData<CardList>(query.queryKey, (current) => {
      if (!current) return current
      return patchCardList(current, next, filtersFromListKey(query.queryKey))
    })
  }
}

/**
 * The decision is known on click, so card caches update immediately.
 * An approval can create an order, which this screen cannot predict, so order
 * lists are invalidated after the write instead of patched in place.
 */
export function useDecideCard() {
  const queryClient = useQueryClient()

  return useMutation<Card, ApiError, DecideCardVariables, { snapshot: CacheSnapshot }>({
    mutationKey: [...cardKeys.all, "decision"],
    mutationFn: ({ cardId, decision }) => postCardDecision(cardId, decision),
    onMutate: async ({ cardId, decision }) => {
      await queryClient.cancelQueries({ queryKey: cardKeys.all })
      const snapshot = snapshotQueries(queryClient, cardKeys.all)
      const current = findCachedCard(cardId, snapshot)
      if (current) {
        const hasDetail = snapshot.some(([queryKey]) =>
          sameQueryKey(queryKey, cardKeys.detail(cardId)),
        )
        patchCardCaches(queryClient, applyCardDecision(current, decision), hasDetail)
      }
      return { snapshot }
    },
    onSuccess: (card) => {
      patchCardCaches(queryClient, card, true)
    },
    onError: (_error, _variables, context) => {
      restoreQueries(queryClient, context?.snapshot)
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
