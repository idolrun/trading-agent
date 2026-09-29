import Link from "next/link"
import type { ReactNode } from "react"

/** Page frame shared by the book screens: back link, h1, content. Server-renderable. */
export function Screen({
  title,
  back,
  children,
}: {
  title: string
  back?: { href: string; label: string }
  children: ReactNode
}) {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6 sm:px-6">
      {back ? (
        <Link href={back.href} className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground">
          {back.label}
        </Link>
      ) : null}
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {children}
    </main>
  )
}
