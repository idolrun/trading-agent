import { queryOptions, useQuery } from "@tanstack/react-query"
import { apiGet } from "@/lib/api/client"
import type { DiscordStatus } from "@/features/discord/types"

export const discordKeys = { status: ["discord", "status"] as const }

export const discordStatusQuery = queryOptions({
  queryKey: discordKeys.status,
  queryFn: () => apiGet<DiscordStatus>("/api/v1/discord/status"),
  staleTime: 5_000,
  refetchInterval: 10_000,
})

export const useDiscordStatus = () => useQuery(discordStatusQuery)
