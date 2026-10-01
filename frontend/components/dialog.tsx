"use client"

import type { ReactNode } from "react"
import {
  Dialog as DialogRoot,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"

const panel =
  "flex max-h-[calc(100%-2rem)] w-[calc(100%-2rem)] max-w-sm flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-background p-5 text-base text-foreground shadow-lg ring-0 animate-none! data-open:animate-none! data-closed:animate-none! sm:max-w-sm"

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
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="bg-black/50 animate-none! supports-backdrop-filter:backdrop-blur-none data-open:animate-none! data-closed:animate-none!"
        className={panel}
        // No description: tell Radix so it does not warn about a missing one.
        {...(description ? {} : { "aria-describedby": undefined })}
      >
        <div>
          <DialogTitle className="font-sans text-lg leading-normal font-semibold">{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </div>
        {children}
      </DialogContent>
    </DialogRoot>
  )
}
