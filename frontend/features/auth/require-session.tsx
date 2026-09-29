"use client"

import { useRouter } from "next/navigation"
import { useEffect, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/features/auth/ceremony"
import { loginUrl, useLogout, useSession } from "@/features/auth/use-session"
import { onUnauthorized } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"

function currentPath() {
  return window.location.pathname + window.location.search
}

/** Client gate for the desk. proxy.ts only checks the cookie exists; this asks the backend. */
export function RequireSession({ children }: { children: ReactNode }) {
  const router = useRouter()
  const session = useSession()
  const logout = useLogout()

  useEffect(() => {
    onUnauthorized(() => {
      if (/^\/(login|enroll)(\/|$)/.test(window.location.pathname)) return
      router.replace(loginUrl(currentPath()))
    })
    return () => onUnauthorized(undefined)
  }, [router])

  const unauthorized = isApiError(session.error) && session.error.status === 401
  useEffect(() => {
    if (unauthorized) router.replace(loginUrl(currentPath()))
  }, [unauthorized, router])

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
  if (!session.data) {
    return (
      <div className="p-6">
        <Spinner label="Checking your session" />
      </div>
    )
  }

  return (
    <>
      <div className="flex justify-end border-b border-border px-4 py-2 sm:px-6">
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
