"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { EVENT_TYPES, applyEvent } from "@/features/live/event-map"

export type LiveStatus = "connecting" | "live" | "reconnecting"

export const EVENTS_URL = "/api/v1/events"
export const MAX_BACKOFF_MS = 30_000

export const backoffMs = (attempt: number) => Math.min(1_000 * 2 ** attempt, MAX_BACKOFF_MS)

function eventId(event: Event): string {
  try {
    return String(JSON.parse((event as MessageEvent<string>).data).id ?? "")
  } catch {
    return ""
  }
}

/**
 * One SSE connection for the desk: events invalidate the matching queries. `EventSource` gives
 * up for good on an HTTP error, so reconnecting is ours (exponential backoff, capped at 30 s).
 * Nothing is replayed, so every reconnect refetches everything.
 */
export function useLiveEvents(): LiveStatus {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<LiveStatus>("connecting")

  useEffect(() => {
    let source: EventSource | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    let dropped = false

    const connect = () => {
      const current = new EventSource(EVENTS_URL, { withCredentials: true })
      source = current
      current.onopen = () => {
        failures = 0
        setStatus("live")
        if (dropped) void queryClient.invalidateQueries()
        dropped = false
      }
      current.onerror = () => {
        current.close()
        dropped = true
        setStatus("reconnecting")
        timer = setTimeout(connect, backoffMs(failures++))
      }
      for (const type of EVENT_TYPES) {
        current.addEventListener(type, (event) => void applyEvent(queryClient, type, eventId(event)))
      }
    }

    connect()
    return () => {
      clearTimeout(timer)
      source?.close()
    }
  }, [queryClient])

  return status
}
