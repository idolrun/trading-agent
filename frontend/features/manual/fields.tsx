"use client"

import { useId, type ReactNode } from "react"
import { cn } from "@/lib/utils"

export const inputClass = "h-11 rounded-md border border-input bg-background px-2 text-sm"
export const textareaClass = "min-h-20 rounded-md border border-input bg-background px-2 py-1.5 text-sm"

/** Label + one control; `children` gets the id to put on the control. */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string
  hint?: string
  className?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className={cn("flex flex-col gap-1 text-sm", className)}>
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {children(id)}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

/** Server or network failure, inline next to the control that caused it. */
export function FormError({ error }: { error: Error | null }) {
  return error ? (
    <p role="alert" className="text-sm text-destructive">
      {error.message}
    </p>
  ) : null
}
