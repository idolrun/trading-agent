import { keepPreviousData, queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { ItemList } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import type { Card, CardListFilters } from "@/features/cards/types"

export const cardKeys = domainKeys("cards")

export type CardList = ItemList<Card>

export function fetchCardList(filters: CardListFilters): Promise<CardList> {
  return apiGet<CardList>("/api/v1/cards", filters)
}

export function fetchCard(cardId: string): Promise<Card> {
  return apiGet<Card>(`/api/v1/cards/${encodeURIComponent(cardId)}`)
}

export function cardListQuery(filters: CardListFilters) {
  return queryOptions({
    queryKey: cardKeys.list(filters),
    queryFn: () => fetchCardList(filters),
    staleTime: 4_000,
    refetchInterval: 8_000,
    placeholderData: keepPreviousData,
  })
}

export function cardDetailQuery(cardId: string) {
  return queryOptions({
    queryKey: cardKeys.detail(cardId),
    queryFn: () => fetchCard(cardId),
    enabled: cardId.length > 0,
    staleTime: 4_000,
    refetchInterval: 8_000,
  })
}

export function useCardList(filters: CardListFilters) {
  return useQuery(cardListQuery(filters))
}

export function useCard(cardId: string) {
  return useQuery(cardDetailQuery(cardId))
}
