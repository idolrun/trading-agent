import axios, { type AxiosError, type AxiosResponse } from "axios"

const NETWORK = "Unable to connect to the server. Check your internet connection and try again."
const TIMED_OUT = "The request timed out. Please try again shortly."
const FALLBACK = "Something went wrong. Please try again."
const UNKNOWN_ORDER = "We couldn't confirm the order status. Check your order history before trying again."

/** Long enough for a joined 422 field list; short of a pasted stack or query dump. */
const MAX_MESSAGE_LENGTH = 2_000

const UNSAFE_MESSAGE = [
  /^request failed with status code \d+$/i,
  /^network error$/i,
  /^timeout of \d+ms exceeded$/i,
  /^internal server error$/i,
  /traceback \(most recent call last\)/i,
  /sqlalchemy|psycopg|sqlite3?/i,
  /syntax error at or near/i,
  /duplicate key value violates/i,
  /<\s*html[\s>]/i,
  /<!doctype/i,
]

type ErrorPayload = {
  message?: unknown
  code?: unknown
  detail?: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string | undefined
  /** No response, a timeout, or a 5xx: the server may have applied the action. */
  readonly ambiguous: boolean

  constructor(message: string, status: number, code?: string, ambiguous = false) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.ambiguous = ambiguous
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

function isSafeMessage(message: string): boolean {
  const text = message.trim()
  if (!text || text.length > MAX_MESSAGE_LENGTH || text.includes("\n")) return false
  return !UNSAFE_MESSAGE.some((pattern) => pattern.test(text))
}

function statusMessage(status: number, code: string | undefined, retryAfterSeconds?: number): string {
  switch (status) {
    case 400:
      return "The request contains invalid data."
    case 401:
      return "Your session may have expired. Please sign in again."
    case 403:
      return "You don't have permission to perform this action."
    case 404:
      return "We couldn't find the requested resource."
    case 409:
      return "The request conflicts with the current state. Refresh and try again."
    case 422:
      return "Some of the information you entered isn't valid. Check the fields and try again."
    case 429:
      return tooManyRequests(retryAfterSeconds)
    case 500:
      return "Something went wrong on our end. Please try again shortly."
    case 502:
    case 503:
      return "The service is temporarily unavailable. Please try again shortly."
    case 504:
      return TIMED_OUT
    default:
      if (status >= 500) return "Something went wrong on our end. Please try again shortly."
      if (status >= 400) return "The request could not be completed."
      return FALLBACK
  }
}

function tooManyRequests(retryAfterSeconds?: number): string {
  if (retryAfterSeconds != null && retryAfterSeconds > 0) {
    const unit = retryAfterSeconds === 1 ? "second" : "seconds"
    return `You're making requests too quickly. Please wait ${retryAfterSeconds} ${unit} and try again.`
  }
  return "You're making requests too quickly. Please wait a moment and try again."
}

function payloadOf(data: unknown): { message: string | undefined; code: string | undefined } {
  if (!data || typeof data !== "object") return { message: undefined, code: undefined }
  const body = data as ErrorPayload
  const code = typeof body.code === "string" && body.code.trim() ? body.code : undefined
  const raw = typeof body.message === "string" && body.message.trim() ? body.message : body.detail
  const message = typeof raw === "string" && isSafeMessage(raw) ? raw.trim() : undefined
  return { message, code }
}

function headerValue(headers: AxiosResponse["headers"], name: string): unknown {
  if (!headers || typeof headers !== "object") return undefined
  if ("get" in headers && typeof headers.get === "function") return headers.get(name)
  const record = headers as Record<string, unknown>
  return record[name] ?? record[name.toLowerCase()]
}

function retryAfterSeconds(headers: AxiosResponse["headers"]): number | undefined {
  const raw = headerValue(headers, "retry-after")
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return Math.ceil(value)
  if (typeof value !== "string" || !/^\d+(\.\d+)?$/.test(value.trim())) return undefined
  return Math.ceil(Number(value))
}

function isTimeout(error: AxiosError): boolean {
  return error.code === "ECONNABORTED" || error.code === "ETIMEDOUT"
}

/** Axios, network, and unexpected throws become an ApiError whose message is safe to show. */
export function apiErrorFrom(error: unknown): ApiError {
  if (!axios.isAxiosError(error)) return new ApiError(FALLBACK, 0)
  if (!error.response) {
    return isTimeout(error) ? new ApiError(TIMED_OUT, 0, "timeout", true) : new ApiError(NETWORK, 0, "network", true)
  }
  const { status, data, headers } = error.response
  const { message, code } = payloadOf(data)
  const ambiguous = status >= 500
  return new ApiError(message ?? statusMessage(status, code, retryAfterSeconds(headers)), status, code, ambiguous)
}

/**
 * Message for an alert or toast. `order` is for a request that can reach the broker:
 * an ambiguous result must not be described as a confirmed failure.
 */
export function userFacingMessage(error: unknown, options?: { order?: boolean }): string {
  if (isApiError(error)) {
    if (options?.order && error.ambiguous) return UNKNOWN_ORDER
    if (isSafeMessage(error.message)) return error.message
    if (error.code === "timeout") return TIMED_OUT
    if (error.code === "network") return NETWORK
    return statusMessage(error.status, error.code)
  }
  if (error instanceof Error && isSafeMessage(error.message)) return error.message
  return FALLBACK
}
