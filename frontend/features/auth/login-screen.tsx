"use client"

import { useQueryClient } from "@tanstack/react-query"
import { Eye, EyeOff } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useId, useState } from "react"
import { deskTitle } from "@/components/screen"
import { ModeToggle } from "@/components/mode-toggle"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { login } from "@/features/auth/api"
import { ErrorAlert, Spinner, TextField, useSubmit } from "@/features/auth/submit"
import { safeNext, sessionKey } from "@/features/auth/use-session"

function PasswordField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="leading-normal select-text">
        Password
      </Label>
      <div className="relative">
        <Input
          id={id}
          type={visible ? "text" : "password"}
          autoComplete="current-password"
          required
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="bg-background py-0 pr-9 text-sm font-normal shadow-none select-text dark:bg-background"
        />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="absolute top-1/2 right-0.5 -translate-y-1/2 text-muted-foreground"
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? <EyeOff /> : <Eye />}
        </Button>
      </div>
    </div>
  )
}

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
    <>
      <div className="flex justify-end px-4 py-2">
        <ModeToggle />
      </div>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
        <div>
          <h1 className={deskTitle}>Sign in</h1>
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
          <PasswordField value={password} onChange={setPassword} />
          <ErrorAlert state={state} />
          {state.phase === "pending" ? <Spinner label="Signing in" /> : null}
          <Button type="submit" size="lg" disabled={state.phase === "pending"}>
            {state.phase === "failed" ? "Try again" : "Sign in"}
          </Button>
        </form>
      </main>
    </>
  )
}
