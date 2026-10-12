import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** One label/value pair inside a `<dl>`. */
export function Field({
  label,
  children,
  large = false,
  className,
}: {
  label: string
  children: ReactNode
  large?: boolean
  className?: string
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-medium tabular-nums break-words", large ? "text-lg" : "text-sm")}>{children}</dd>
    </div>
  )
}

/** A field the interpreter took from an earlier message is marked in place. */
export function Inferred({ show, children }: { show: boolean; children: ReactNode }) {
  if (!show) return <>{children}</>
  return (
    <mark
      title="Inferred from an earlier message"
      className="rounded-sm bg-warning-muted px-0.5 text-warning-foreground underline decoration-dotted"
    >
      {children}
    </mark>
  )
}
