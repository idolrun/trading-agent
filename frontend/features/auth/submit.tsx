"use client"

import { useCallback, useEffect, useRef, useState, type InputHTMLAttributes } from "react"

export type SubmitState = { phase: "idle" | "pending" } | { phase: "failed"; message: string }

const IDLE: SubmitState = { phase: "idle" }

/**
 * Runs one submit. On success the state stays "pending" so the button stays
 * disabled while the caller navigates or closes.
 */
export function useSubmit() {
  const [state, setState] = useState<SubmitState>(IDLE)

  const run = useCallback(async (action: () => Promise<void>): Promise<boolean> => {
    setState({ phase: "pending" })
    try {
      await action()
      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : "Something went wrong. Try again."
      setState({ phase: "failed", message })
      return false
    }
  }, [])

  const reset = useCallback(() => setState(IDLE), [])
  return { state, run, reset }
}

/** Failure message. Takes focus when it appears so screen readers announce it. */
export function ErrorAlert({ state }: { state: SubmitState }) {
  const ref = useRef<HTMLDivElement>(null)
  const failed = state.phase === "failed"
  useEffect(() => {
    if (failed) ref.current?.focus()
  }, [failed, state])

  if (state.phase !== "failed") return null
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm outline-none"
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

export function TextField({ label, ...input }: { label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium">
      {label}
      <input
        {...input}
        className="h-9 rounded-md border border-input bg-background px-2.5 text-sm font-normal outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
    </label>
  )
}
