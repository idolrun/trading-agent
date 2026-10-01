import type { ReactNode } from "react"
import { Badge as UiBadge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const chip =
  "h-auto shrink overflow-visible rounded-md bg-muted whitespace-normal [&>svg]:size-3.5!"

export function Badge({
  children,
  className,
  asChild = false,
}: {
  children: ReactNode
  className?: string
  asChild?: boolean
}) {
  return (
    <UiBadge asChild={asChild} variant="outline" className={cn(chip, className)}>
      {children}
    </UiBadge>
  )
}
