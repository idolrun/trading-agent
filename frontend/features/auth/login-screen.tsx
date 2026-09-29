"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { login } from "@/features/auth/api"
import { ErrorAlert, Spinner, TextField, useSubmit } from "@/features/auth/submit"
import { safeNext, sessionKey } from "@/features/auth/use-session"

export function LoginScreen() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const next = safeNext(useSearchParams().get("next"))
  const { state, run } = useSubmit()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")

  const signIn = async () => {
    let mustChange = false
    const ok = await run(async () => {
      mustChange = (await login(email.trim(), password)).mustChangePassword
    })
    if (!ok) return
    queryClient.removeQueries({ queryKey: sessionKey })
    router.replace(mustChange ? "/change-password" : next)
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">Use your email and password to open the desk.</p>
      </div>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          void signIn()
        }}
      >
        <TextField
          label="Email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <ErrorAlert state={state} />
        {state.phase === "pending" ? <Spinner label="Signing in" /> : null}
        <Button type="submit" size="lg" disabled={state.phase === "pending"}>
          {state.phase === "failed" ? "Try again" : "Sign in"}
        </Button>
      </form>
    </main>
  )
}
