import { useCallback, useRef, useState } from "react"
import { stepUp } from "@/features/auth/api"
import { useSubmit } from "@/features/auth/submit"
import { ApiError } from "@/lib/api/errors"

export class StepUpCancelledError extends Error {
  constructor() {
    super("Password confirmation was cancelled")
    this.name = "StepUpCancelledError"
  }
}

export const isStepUpRequired = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status === 403 && error.code === "step_up_required"

type Waiter = { promise: Promise<void>; resolve: () => void; reject: (error: Error) => void }

/**
 * Password step-up: `ensureStepUp()` opens `<StepUpDialog {...dialogProps} />` and settles
 * once the user confirms (resolves) or cancels (rejects).
 */
export function useStepUp() {
  const { state, run, reset } = useSubmit()
  const [open, setOpen] = useState(false)
  const waiter = useRef<Waiter | null>(null)

  const ensureStepUp = useCallback((): Promise<void> => {
    if (!waiter.current) {
      let resolve!: () => void
      let reject!: (error: Error) => void
      const promise = new Promise<void>((res, rej) => {
        resolve = res
        reject = rej
      })
      waiter.current = { promise, resolve, reject }
      reset()
      setOpen(true)
    }
    return waiter.current.promise
  }, [reset])

  const onConfirm = useCallback(
    async (password: string) => {
      const ok = await run(async () => {
        await stepUp(password)
      })
      if (!ok) return
      waiter.current?.resolve()
      waiter.current = null
      setOpen(false)
    },
    [run],
  )

  const onCancel = useCallback(() => {
    waiter.current?.reject(new StepUpCancelledError())
    waiter.current = null
    setOpen(false)
  }, [])

  /**
   * Wraps a mutation function: on `403 step_up_required`, confirms with the
   * password and retries ONCE. A second failure surfaces as is.
   */
  const withStepUp = useCallback(
    <Args extends unknown[], Result>(fn: (...args: Args) => Promise<Result>) =>
      async (...args: Args): Promise<Result> => {
        try {
          return await fn(...args)
        } catch (error) {
          if (!isStepUpRequired(error)) throw error
          await ensureStepUp()
          return fn(...args)
        }
      },
    [ensureStepUp],
  )

  return { ensureStepUp, withStepUp, dialogProps: { open, state, onConfirm, onCancel } }
}
