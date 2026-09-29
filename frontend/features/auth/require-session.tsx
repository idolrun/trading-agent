"use client"

import { useRouter } from "next/navigation"
import { useEffect, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/features/auth/submit"
import { loginUrl, useLogout, useSession } from "@/features/auth/use-session"
import { onUnauthorized } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"

function currentPath() {
  return window.location.pathname + window.location.search
}

/** Client gate for the desk. proxy.ts only checks the cookie exists; this asks the backend. */
export function RequireSession({ children, header }: { children: ReactNode; header?: ReactNode }) {
  const router = useRouter()
  const session = useSession()
  const logout = useLogout()

  useEffect(() => {
    onUnauthorized(() => {
      if (/^\/login(\/|$)/.test(window.location.pathname)) return
      router.replace(loginUrl(currentPath()))
    })
    return () => onUnauthorized(undefined)
  }, [router])

  const unauthorized = isApiError(session.error) && session.error.status === 401
  useEffect(() => {
    if (unauthorized) router.replace(loginUrl(currentPath()))
  }, [unauthorized, router])

  // /change-password sits outside the desk: a temporary password blocks everything else.
  const mustChange = session.data?.mustChangePassword === true
  useEffect(() => {
    if (mustChange) router.replace("/change-password")
  }, [mustChange, router])

  if (session.isError && !unauthorized) {
    return (
      <div role="alert" className="mx-auto flex max-w-sm flex-col items-start gap-3 p-6">
        <p className="text-sm">{session.error.message}</p>
        <Button type="button" variant="outline" size="sm" onClick={() => void session.refetch()}>
          Retry
        </Button>
      </div>
    )
  }
  if (!session.data || mustChange) {
    return (
      <div className="p-6">
        <Spinner label="Checking your session" />
      </div>
    )
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 border-b border-border px-4 py-2 sm:px-6">
        {header}
        <span className="text-sm text-muted-foreground">{session.data.email}</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={logout.isPending}
          onClick={() => logout.mutate()}
        >
          Sign out
        </Button>
      </div>
      {children}
    </>
  )
}
