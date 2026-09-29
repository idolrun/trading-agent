"use client"

import { startAuthentication } from "@simplewebauthn/browser"
import { useQueryClient } from "@tanstack/react-query"
import { useRouter, useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { loginOptions, loginVerify } from "@/features/auth/api"
import { CeremonyAlert, Spinner, Unsupported, useCeremony, useWebAuthnSupported } from "@/features/auth/ceremony"
import { safeNext, sessionKey } from "@/features/auth/use-session"

export function LoginScreen() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const next = safeNext(useSearchParams().get("next"))
  const supported = useWebAuthnSupported()
  const { state, run } = useCeremony()

  const signIn = async () => {
    const ok = await run(async () => {
      const optionsJSON = await loginOptions()
      const credential = await startAuthentication({ optionsJSON })
      await loginVerify(credential)
    })
    if (!ok) return
    queryClient.removeQueries({ queryKey: sessionKey })
    router.replace(next)
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">Use your passkey to open the desk.</p>
      </div>
      {supported ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void signIn()
          }}
        >
          <CeremonyAlert state={state} />
          {state.phase === "pending" ? <Spinner label="Waiting for your passkey" /> : null}
          <Button type="submit" size="lg" disabled={state.phase === "pending"}>
            {state.phase === "failed" ? "Try again" : "Sign in with passkey"}
          </Button>
        </form>
      ) : (
        <Unsupported />
      )}
    </main>
  )
}
