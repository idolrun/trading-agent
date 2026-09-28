import { isApiError } from "@/lib/api/errors"

/** Retry transport failures and 5xx. A 4xx is the server's answer. */
export function retryServerErrors(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500) return false
  return failureCount < 2
}
