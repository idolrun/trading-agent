import axios, { type AxiosError } from "axios"
import { ApiError } from "@/lib/api/errors"
import { stableFilters } from "@/lib/query/keys"

const http = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || undefined,
  headers: { Accept: "application/json" },
})

type ErrorBody = {
  message?: string
  code?: string
}

function toApiError(error: unknown): ApiError {
  if (!axios.isAxiosError(error)) {
    return new ApiError("Network error", 0, "network")
  }
  const axiosError = error as AxiosError<ErrorBody>
  if (!axiosError.response) {
    return new ApiError(axiosError.message || "Network error", 0, "network")
  }
  return new ApiError(
    axiosError.response.data?.message || axiosError.message,
    axiosError.response.status,
    axiosError.response.data?.code,
  )
}

export async function apiGet<T>(path: string, filters?: object): Promise<T> {
  try {
    const response = await http.get<T>(path, {
      params: filters ? stableFilters(filters) : undefined,
    })
    return response.data
  } catch (error) {
    throw toApiError(error)
  }
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  try {
    const response = await http.post<T>(path, body)
    return response.data
  } catch (error) {
    throw toApiError(error)
  }
}
