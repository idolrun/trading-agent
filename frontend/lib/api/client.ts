import axios, { type AxiosError } from "axios"
import { ApiError } from "@/lib/api/errors"
import type { Schemas } from "@/lib/api/types"
import { stableFilters } from "@/lib/query/keys"

// Same-origin: next.config.ts proxies /api/* to the backend, so the session
// cookie stays SameSite=Strict and the WebAuthn RP ID is the dashboard host.
const http = axios.create({
  headers: { Accept: "application/json" },
})

type ErrorBody = Partial<Schemas["ErrorBody"]>

type ErrorHandler = (error: ApiError) => void

const handlers: { unauthorized?: ErrorHandler; stepUpRequired?: ErrorHandler } = {}

/** 401: session missing or expired. F053 registers the redirect to /login. */
export function onUnauthorized(handler: ErrorHandler | undefined) {
  handlers.unauthorized = handler
}

/** 403 `step_up_required`: F053 registers the step-up prompt. */
export function onStepUpRequired(handler: ErrorHandler | undefined) {
  handlers.stepUpRequired = handler
}

function toApiError(error: unknown): ApiError {
  if (!axios.isAxiosError(error)) {
    return new ApiError("Network error", 0, "network")
  }
  const axiosError = error as AxiosError<ErrorBody>
  if (!axiosError.response) {
    return new ApiError(axiosError.message || "Network error", 0, "network")
  }
  const { status, data } = axiosError.response
  return new ApiError(data?.message || axiosError.message, status, data?.code)
}

function fail(error: unknown): never {
  const apiError = toApiError(error)
  if (apiError.status === 401) handlers.unauthorized?.(apiError)
  else if (apiError.status === 403 && apiError.code === "step_up_required") {
    handlers.stepUpRequired?.(apiError)
  }
  throw apiError
}

export async function apiGet<T>(path: string, filters?: object): Promise<T> {
  try {
    const response = await http.get<T>(path, {
      params: filters ? stableFilters(filters) : undefined,
    })
    return response.data
  } catch (error) {
    return fail(error)
  }
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  try {
    const response = await http.post<T>(path, body)
    return response.data
  } catch (error) {
    return fail(error)
  }
}
