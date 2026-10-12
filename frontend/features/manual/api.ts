import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "@/components/toaster"
import { cardKeys } from "@/features/cards/queries"
import { healthKeys } from "@/features/health/queries"
import { lotKeys } from "@/features/lots/queries"
import type { LotDetail } from "@/features/lots/types"
import { orderKeys } from "@/features/orders/queries"
import { positionKeys } from "@/features/positions/queries"
import type { PositionDetail } from "@/features/positions/types"
import { apiGet, apiPatch, apiPost, apiPut } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"

export type ExitBody = Omit<Schemas["ExitRequest"], "idempotencyKey">
export type StopBody = Omit<Schemas["StopRequest"], "idempotencyKey">
export type EntryBody = Omit<Schemas["EntryRequest"], "idempotencyKey">
export type FlattenScope = Schemas["FlattenRequest"]["scope"]
export type ManualResult = Schemas["ManualResultOut"]
export type FlattenResult = Schemas["FlattenOut"]
export type Profile = Schemas["ProfileOut"]
export type ProfileList = "aliases" | "lexicon" | "habits"
export const PROFILE_LISTS: readonly ProfileList[] = ["aliases", "lexicon", "habits"]

const V1 = "/api/v1"
const id = encodeURIComponent

export const postExit = (body: ExitBody, idempotencyKey: string) =>
  apiPost<ManualResult>(`${V1}/manual/exit`, { ...body, idempotencyKey })
export const postStop = (body: StopBody, idempotencyKey: string) =>
  apiPost<ManualResult>(`${V1}/manual/stops`, { ...body, idempotencyKey })
export const postEntry = (body: EntryBody, idempotencyKey: string) =>
  apiPost<ManualResult>(`${V1}/manual/entry`, { ...body, idempotencyKey })
export const postFlatten = (scope: FlattenScope, idempotencyKey: string) =>
  apiPost<FlattenResult>(`${V1}/flatten`, { scope, idempotencyKey })
export const postKillSwitch = (body: Schemas["KillSwitchRequest"]) =>
  apiPost<Schemas["KillSwitchOut"]>(`${V1}/kill-switch`, body)
export const postAdjust = (lotId: string, body: Schemas["AdjustRequest"]) =>
  apiPost<LotDetail>(`${V1}/lots/${id(lotId)}/adjust`, body)
export const postPosition = (body: Schemas["AddPositionRequest"]) =>
  apiPost<PositionDetail>(`${V1}/positions`, body)
export const patchPosition = (positionId: string, body: Schemas["EditPositionRequest"]) =>
  apiPatch<PositionDetail>(`${V1}/positions/${id(positionId)}`, body)
export const postLink = (body: Schemas["RelinkRequest"]) => apiPost<Schemas["RelinkOut"]>(`${V1}/links`, body)
// Provider ids are snowflakes: they stay strings here, in the URL and in bodies (the generated type says number).
export const fetchProfile = (providerId: string) => apiGet<Profile>(`${V1}/providers/${id(providerId)}/profile`)
export const putProfile = (providerId: string, body: Schemas["ProfileRequest"]) =>
  apiPut<Profile>(`${V1}/providers/${id(providerId)}/profile`, body)

export const profileKey = (providerId: string) => ["providers", providerId, "profile"] as const
export const useProfile = (providerId: string, enabled = true) =>
  useQuery({ queryKey: profileKey(providerId), queryFn: () => fetchProfile(providerId), enabled })

/** These mutations emit no SSE, so refresh everything they can touch. */
export function invalidateBook(queryClient: QueryClient) {
  return Promise.all(
    [lotKeys.all, positionKeys.all, orderKeys.all, cardKeys.all, ["providers"]].map((queryKey) =>
      queryClient.invalidateQueries({ queryKey }),
    ),
  ).then(() => queryClient.invalidateQueries({ queryKey: healthKeys.all }))
}

/**
 * One idempotency key per mounted form (= per dialog open). It stays the same
 * across any retry after a network/5xx failure, and rotates
 * after a success or a 422 (the backend binds the key to the refusal and would
 * replay it, so a corrected resubmit needs a new key).
 */
function useIdempotencyKey() {
  const [key, setKey] = useState(() => crypto.randomUUID())
  return { key, rotate: () => setKey(crypto.randomUUID()) }
}

type Options<Vars, Result> = {
  /** `key` is the current idempotency key; ignore it for endpoints that take none. */
  call: (vars: Vars, key: string) => Promise<Result>
  success: string | ((result: Result) => string)
  /** Runs after a successful call (close the dialog, reset the form). */
  onDone?: (result: Result) => void
}

export function useManualMutation<Vars, Result>({ call, success, onDone }: Options<Vars, Result>) {
  const queryClient = useQueryClient()
  const { key, rotate } = useIdempotencyKey()

  return useMutation<Result, Error, Vars>({
    mutationFn: (vars) => call(vars, key),
    onSuccess: (result) => {
      rotate()
      toast(typeof success === "function" ? success(result) : success)
      onDone?.(result)
    },
    onError: (error) => {
      if ("status" in error && error.status === 422) rotate()
    },
    onSettled: () => invalidateBook(queryClient),
  })
}
