"use client"

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"
import { describeCeremonyError, webAuthnSupported, type CeremonyOutcome } from "@/features/auth/api"

export type CeremonyState = { phase: "idle" | "pending" } | ({ phase: "failed" } & CeremonyOutcome)

const noSubscribe = () => () => {}

/** Server render and first hydration assume support, so markup matches. */
export function useWebAuthnSupported() {
  return useSyncExternalStore(noSubscribe, webAuthnSupported, () => true)
}

const IDLE: CeremonyState = { phase: "idle" }

/**
 * Runs one passkey ceremony. On success the state stays "pending" so the
 * button stays disabled while the caller navigates or closes.
 */
export function useCeremony() {
  const [state, setState] = useState<CeremonyState>(IDLE)

  const run = useCallback(async (ceremony: () => Promise<void>): Promise<boolean> => {
    setState({ phase: "pending" })
    try {
      await ceremony()
      return true
    } catch (error) {
      setState({ phase: "failed", ...describeCeremonyError(error) })
      return false
    }
  }, [])

  const reset = useCallback(() => setState(IDLE), [])
  return { state, run, reset }
}

/** Failure message. Takes focus when it appears so screen readers announce it. */
export function CeremonyAlert({ state }: { state: CeremonyState }) {
  const ref = useRef<HTMLDivElement>(null)
  const failed = state.phase === "failed"
  useEffect(() => {
    if (failed) ref.current?.focus()
  }, [failed, state])

  if (state.phase !== "failed") return null
  const cancelled = state.kind === "cancelled"
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role={cancelled ? "status" : "alert"}
      className={
        cancelled
          ? "rounded-md border border-border px-3 py-2 text-sm text-muted-foreground outline-none"
          : "rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm outline-none"
      }
    >
      {state.message}
    </div>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <p role="status" aria-busy="true" className="flex items-center gap-2 text-sm text-muted-foreground">
      <span aria-hidden className="size-4 animate-spin rounded-full border-2 border-border border-t-foreground" />
      {label}
    </p>
  )
}

export function Unsupported() {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm">
      This browser does not support passkeys. Open the dashboard in a current version of Safari,
      Chrome, Edge or Firefox.
    </div>
  )
}
