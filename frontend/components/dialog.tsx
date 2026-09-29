"use client"

import type { ReactNode } from "react"
import { Dialog as Radix } from "radix-ui"

/** Modal with a focus trap, title and description. Content is whatever the caller passes. */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children?: ReactNode
}) {
  return (
    <Radix.Root open={open} onOpenChange={onOpenChange}>
      <Radix.Portal>
        <Radix.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Radix.Content
          // No description: tell Radix so it does not warn about a missing one.
          {...(description ? {} : { "aria-describedby": undefined })}
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100%-2rem)] w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-background p-5 shadow-lg"
        >
          <div>
            <Radix.Title className="text-lg font-semibold">{title}</Radix.Title>
            {description ? (
              <Radix.Description className="text-sm text-muted-foreground">{description}</Radix.Description>
            ) : null}
          </div>
          {children}
        </Radix.Content>
      </Radix.Portal>
    </Radix.Root>
  )
}
