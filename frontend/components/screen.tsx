import Link from "next/link"
import type { ReactNode } from "react"

/** One desk width and one vertical rhythm. Login stays narrower. */
export const deskFrame = "mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6"
export const deskTitle = "font-heading text-2xl font-semibold tracking-tight"

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
    <div className={deskFrame}>
      {back ? (
        <Link href={back.href} className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground">
          {back.label}
        </Link>
      ) : null}
      <h1 className={deskTitle}>{title}</h1>
      {children}
    </div>
  )
}
