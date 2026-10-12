"use client"

import { useRouter } from "next/navigation"
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Spinner } from "@/features/auth/submit"
import { UserMenu } from "@/features/auth/user-menu"
import { loginUrl, useLogout, useSession } from "@/features/auth/use-session"
import { onUnauthorized } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"

function currentPath() {
  return window.location.pathname + window.location.search
}

/** Client gate for the desk. proxy.ts only checks the cookie exists; this asks the backend. */
export function RequireSession({
  children,
  header,
  sidebar,
  actions,
  menu,
}: {
  children: ReactNode
  header?: ReactNode
  sidebar?: ReactNode
  actions?: ReactNode
  /** Extra rows in the account menu (notifications). */
  menu?: ReactNode
}) {
  const router = useRouter()
  const session = useSession()
  const logout = useLogout()
  const headerRef = useRef<HTMLElement>(null)

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

  const headerVisible = Boolean(session.data) && !mustChange && !(session.isError && !unauthorized)
  // The feed's "New messages" pill sticks under this bar. The bar wraps, so its height is measured.
  useLayoutEffect(() => {
    const node = headerRef.current
    if (!node) return
    const publish = () => {
      const height = Math.ceil(node.getBoundingClientRect().height)
      if (height > 0) document.documentElement.style.setProperty("--desk-header-height", `${height}px`)
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(node)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty("--desk-header-height")
    }
  }, [headerVisible])

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

  const bar = (
    <header
      ref={headerRef}
      className="sticky top-0 z-20 flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-background px-4 py-1.5 has-[[data-mode=live]]:border-t-4 has-[[data-mode=live]]:border-t-destructive sm:px-6"
    >
      {sidebar ? (
        <>
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-4 self-center" />
        </>
      ) : null}
      {header}
      <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-2">
        {actions}
        <UserMenu email={session.data.email} signingOut={logout.isPending} onSignOut={() => logout.mutate()}>
          {menu}
        </UserMenu>
      </div>
    </header>
  )

  if (!sidebar) {
    return (
      <>
        {bar}
        {children}
      </>
    )
  }

  return (
    <TooltipProvider>
      <SidebarProvider>
        {sidebar}
        <SidebarInset className="min-w-0">
          {bar}
          {children}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
