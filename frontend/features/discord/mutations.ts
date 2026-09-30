import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "@/components/toaster"
import { apiPut } from "@/lib/api/client"
import type { ApiError } from "@/lib/api/errors"
import { configKeys, fetchConfig } from "@/features/config/queries"
import type { ConfigResponse } from "@/features/config/types"
import { discordKeys } from "@/features/discord/queries"

export type ChannelChange = { action: "add" | "remove"; channelId: string }

/**
 * Watched channels are `channels` in the runtime config. Read the current document, change that
 * one list, save against the version just read. A 409 (someone saved in between) surfaces as
 * the error and a retry re-reads.
 */
export function useChangeChannel() {
  const queryClient = useQueryClient()
  return useMutation<ConfigResponse, ApiError, ChannelChange>({
    mutationFn: async ({ action, channelId }) => {
      const current = await fetchConfig()
      const others = ((current.document.channels as string[] | undefined) ?? []).map(String).filter((id) => id !== channelId)
      const channels = action === "add" ? [...others, channelId] : others
      return apiPut<ConfigResponse>("/api/v1/config", {
        expectedVersion: current.version,
        document: { ...current.document, channels },
        reason: `${action === "add" ? "Watch" : "Stop watching"} Discord channel ${channelId}`,
      })
    },
    onSuccess: (saved, { action }) => {
      queryClient.setQueryData(configKeys.detail("current"), saved)
      toast(action === "add" ? "Channel added" : "Channel removed")
    },
    onSettled: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: discordKeys.status }),
      queryClient.invalidateQueries({ queryKey: configKeys.lists() }),
    ]),
  })
}
