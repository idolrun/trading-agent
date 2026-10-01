"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { deskTitle } from "@/components/screen"
import { ModeToggle } from "@/components/mode-toggle"
import { Button } from "@/components/ui/button"
import { changePassword } from "@/features/auth/api"
import { ErrorAlert, Spinner, TextField, useSubmit } from "@/features/auth/submit"
import { loginUrl, sessionKey, useSession } from "@/features/auth/use-session"
import { isApiError } from "@/lib/api/errors"

const MIN_LENGTH = 12

export function ChangePasswordScreen() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const session = useSession()
  const { state, run } = useSubmit()
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const signedOut = isApiError(session.error) && session.error.status === 401
  useEffect(() => {
    if (signedOut) router.replace(loginUrl("/change-password"))
  }, [signedOut, router])
  const mismatch = confirm !== "" && confirm !== next

  const submit = async () => {
    const ok = await run(() => changePassword(current, next))
    if (!ok) return
    await queryClient.invalidateQueries({ queryKey: sessionKey })
    router.replace("/")
  }

  return (
    <>
      <div className="flex justify-end px-4 py-2">
        <ModeToggle />
      </div>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
        <div>
          <h1 className={deskTitle}>Change your password</h1>
          <p className="text-sm text-muted-foreground">
            {session.data?.mustChangePassword
              ? "Replace your temporary password to continue."
              : "Other devices are signed out when you change it."}
          </p>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
          <TextField
            label={`New password (at least ${MIN_LENGTH} characters)`}
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_LENGTH}
            value={next}
            onChange={(event) => setNext(event.target.value)}
          />
          <TextField
            label="Repeat new password"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
          {mismatch ? (
            <p role="alert" className="text-sm text-destructive">
              The passwords do not match.
            </p>
          ) : null}
          <ErrorAlert state={state} />
          {state.phase === "pending" ? <Spinner label="Saving" /> : null}
          <Button type="submit" size="lg" disabled={state.phase === "pending" || mismatch}>
            Change password
          </Button>
        </form>
      </main>
    </>
  )
}
