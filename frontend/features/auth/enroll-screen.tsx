"use client"

import { startRegistration } from "@simplewebauthn/browser"
import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { registerOptions, registerVerify } from "@/features/auth/api"
import { CeremonyAlert, Spinner, Unsupported, useCeremony, useWebAuthnSupported } from "@/features/auth/ceremony"

export function EnrollScreen() {
  const router = useRouter()
  const token = useSearchParams().get("token")
  const supported = useWebAuthnSupported()
  const { state, run } = useCeremony()
  const [label, setLabel] = useState("")

  const enroll = async (enrollmentToken: string) => {
    const ok = await run(async () => {
      const optionsJSON = await registerOptions(enrollmentToken, label.trim())
      const credential = await startRegistration({ optionsJSON })
      await registerVerify(enrollmentToken, credential)
    })
    // Registering does not sign in; `/` would bounce to /login anyway.
    if (ok) router.replace("/login")
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Add a passkey</h1>
        <p className="text-sm text-muted-foreground">
          Register this device with the enrolment link from the console.
        </p>
      </div>
      {!supported ? (
        <Unsupported />
      ) : !token ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm">
          This enrolment link has no token. Ask the console for a new one.
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void enroll(token)
          }}
        >
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Device name
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              required
              maxLength={64}
              autoComplete="off"
              placeholder="iPhone"
              className="h-9 rounded-md border border-input bg-background px-2.5 text-sm font-normal outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </label>
          <CeremonyAlert state={state} />
          {state.phase === "pending" ? <Spinner label="Waiting for your passkey" /> : null}
          <Button type="submit" size="lg" disabled={state.phase === "pending" || label.trim() === ""}>
            {state.phase === "failed" ? "Try again" : "Create passkey"}
          </Button>
        </form>
      )}
    </main>
  )
}
