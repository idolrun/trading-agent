import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "@/components/toaster"
import { apiPut } from "@/lib/api/client"
import type { ApiError } from "@/lib/api/errors"
import { labelKeys } from "@/features/labels/queries"
import type { Label, LabelDetail } from "@/features/labels/types"

export type SaveLabelVars = { eventKey: string; label: Label }

/** Confirm, "no signal" and corrections are all one PUT of a label. 409: someone labeled it first. */
export function useSaveLabel() {
  const queryClient = useQueryClient()
  return useMutation<LabelDetail, ApiError, SaveLabelVars>({
    mutationFn: ({ eventKey, label }) => apiPut<LabelDetail>(`/api/v1/labels/${encodeURIComponent(eventKey)}`, { label }),
    onError: (error) =>
      toast(error.status === 409 ? "This message was labeled elsewhere. Queue refreshed." : `Couldn't save: ${error.message}`),
    // Not returned: awaiting the refetch would unmount the editor before its per-call onSuccess (advance) runs.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: labelKeys.all })
    },
  })
}
