import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "@/components/toaster"
import { apiPut } from "@/lib/api/client"
import type { ApiError } from "@/lib/api/errors"
import { configKeys } from "@/features/config/queries"
import type { ConfigDoc, ConfigResponse } from "@/features/config/types"

export type SaveConfigVars = { expectedVersion: number; document: ConfigDoc; reason: string }

/** Full document + the version last loaded/saved. 409/422 are handled by the screen. */
export function useSaveConfig() {
  const queryClient = useQueryClient()
  return useMutation<ConfigResponse, ApiError, SaveConfigVars>({
    mutationFn: (body) => apiPut<ConfigResponse>("/api/v1/config", body),
    onSuccess: (saved) => {
      queryClient.setQueryData(configKeys.detail("current"), saved)
      toast(`Config saved (version ${saved.version})`)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: configKeys.lists() }),
  })
}
