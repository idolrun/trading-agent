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
 *
 * Hidden tabs let go of the stream. Over HTTP/1.1 the browser allows 6 connections per host
 * across all tabs and an open stream holds one for good, so 6 open desk tabs left every new
 * tab's requests (first `/auth/session`) queued forever behind "Checking your session".
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
      clearTimeout(timer)
      if (document.hidden) {
        dropped = true // events may be missed until the tab is shown
        return
      }
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
        source = undefined
        dropped = true
        setStatus("reconnecting")
        timer = setTimeout(connect, backoffMs(failures++))
      }
      for (const type of EVENT_TYPES) {
        current.addEventListener(type, (event) => void applyEvent(queryClient, type, eventId(event)))
      }
    }

    const onVisibility = () => {
      if (document.hidden) {
        clearTimeout(timer)
        source?.close()
        source = undefined
        dropped = true
      } else if (!source) {
        failures = 0
        connect()
      }
    }

    connect()
    document.addEventListener("visibilitychange", onVisibility)
    return () => {
      document.removeEventListener("visibilitychange", onVisibility)
      clearTimeout(timer)
      source?.close()
    }
  }, [queryClient])

  return status
}
