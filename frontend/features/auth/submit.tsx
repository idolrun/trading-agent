"use client"

import { useCallback, useEffect, useRef, useState, type InputHTMLAttributes } from "react"
import { Alert } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner as SpinnerIcon } from "@/components/ui/spinner"
import { userFacingMessage } from "@/lib/api/errors"

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
      const message = userFacingMessage(error)
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
    <Alert
      ref={ref}
      tabIndex={-1}
      variant="destructive"
      className="rounded-md border-destructive/30 bg-destructive/5 px-3 py-2 text-foreground outline-none"
    >
      {state.message}
    </Alert>
  )
}

export function Spinner({ label }: { label: string }) {
  return (
    <p role="status" aria-busy="true" className="flex items-center gap-2 text-sm text-muted-foreground">
      <SpinnerIcon aria-hidden role="presentation" aria-label={undefined} />
      {label}
    </p>
  )
}

export function TextField({ label, ...input }: { label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <Label className="flex-col items-stretch gap-1.5 leading-normal select-text">
      {label}
      <Input
        {...input}
        className="bg-background py-0 text-sm font-normal shadow-none select-text dark:bg-background"
      />
    </Label>
  )
}
