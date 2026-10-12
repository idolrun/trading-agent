"use client"

import { useId, type ReactNode } from "react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Field as UiField, FieldDescription, FieldLabel } from "@/components/ui/field"
import { userFacingMessage } from "@/lib/api/errors"

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
    <UiField className={className}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children(id)}
      {hint ? <FieldDescription>{hint}</FieldDescription> : null}
    </UiField>
  )
}

/** Server or network failure, inline next to the control that caused it. */
export function FormError({ error, order = false }: { error: Error | null; order?: boolean }) {
  return error ? (
    <Alert variant="destructive">
      <AlertDescription>{userFacingMessage(error, { order })}</AlertDescription>
    </Alert>
  ) : null
}
