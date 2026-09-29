"use client"

import { Clock } from "lucide-react"
import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

/** Ticks once a second until `validUntil`. Display only: the server decides expiry. */
export function useExpired(validUntil: string | null): boolean {
  const deadline = validUntil == null ? null : Date.parse(validUntil)
  const [now, setNow] = useState(() => Date.now())
  const expired = deadline != null && now >= deadline

  useEffect(() => {
    if (deadline == null || expired) return
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [deadline, expired])

  return expired
}

function remainingText(validUntil: string): string {
  const seconds = (Date.parse(validUntil) - Date.now()) / 1_000
  return seconds >= 60 ? `${Math.ceil(seconds / 60)} min left` : "Under 1 min left"
}

/** The text changes once a minute (then once at expiry), so the polite live region stays quiet. */
export function Countdown({ validUntil }: { validUntil: string | null }) {
  const expired = useExpired(validUntil)
  if (validUntil == null) return null

  return (
    <p
      aria-live="polite"
      data-expired={expired}
      className={cn(
        "inline-flex items-center gap-1 text-sm tabular-nums",
        expired ? "text-muted-foreground line-through" : "text-foreground",
      )}
    >
      <Clock aria-hidden className="size-4" />
      <span>{expired ? "Expired" : remainingText(validUntil)}</span>
    </p>
  )
}
