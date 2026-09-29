"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"

const LINKS = [
  { href: "/", label: "Inbox" },
  { href: "/feed", label: "Feed" },
  { href: "/positions", label: "Positions" },
  { href: "/orders", label: "Orders" },
  { href: "/alerts", label: "Alerts" },
  { href: "/health", label: "Health" },
]

export function DeskNav() {
  const pathname = usePathname()
  return (
    <nav aria-label="Desk" className="flex items-center gap-1">
      {LINKS.map(({ href, label }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href)
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-2.5 py-1.5 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50",
              active ? "bg-muted font-medium underline underline-offset-4" : "text-muted-foreground",
            )}
          >
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
