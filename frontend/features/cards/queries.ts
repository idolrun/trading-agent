import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"
import { domainKeys } from "@/lib/query/keys"
import { orderListQuery } from "@/features/orders/queries"
import type { CardDetail, CardListFilters } from "@/features/cards/types"

export const cardKeys = domainKeys("cards")

export type CardPage = Schemas["Page_CardOut_"]

export function fetchCardPage(filters: CardListFilters, cursor?: string): Promise<CardPage> {
  return apiGet<CardPage>("/api/v1/cards", { ...filters, cursor })
}

export function fetchCard(cardId: string): Promise<CardDetail> {
  return apiGet<CardDetail>(`/api/v1/cards/${encodeURIComponent(cardId)}`)
}

/** Pages are followed by "Load more"; a refetch (SSE, poll) reloads every loaded page. */
export function cardListQuery(filters: CardListFilters) {
  return infiniteQueryOptions({
    queryKey: cardKeys.list(filters),
    queryFn: ({ pageParam }) => fetchCardPage(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    staleTime: 4_000,
    refetchInterval: 8_000,
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
  return useInfiniteQuery({
    ...cardListQuery(filters),
    select: (data) => ({
      items: data.pages.flatMap((page) => page.items),
    }),
  })
}

export function useCard(cardId: string) {
  return useQuery(cardDetailQuery(cardId))
}

/** The broker order a card became, once it has a lot. Matched by card id among the lot's orders. */
export function useCardOrder(card: { id: string; lotId: string | null }) {
  const lotId = card.lotId
  return useInfiniteQuery({
    ...orderListQuery({ lotId }),
    enabled: lotId != null,
    select: (data) => data.pages.flatMap((page) => page.items).find((order) => order.cardId === card.id) ?? null,
  })
}
