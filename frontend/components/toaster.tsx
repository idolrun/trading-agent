"use client"

import { X } from "lucide-react"
import { useSyncExternalStore } from "react"

type Toast = { id: number; message: string }

const NONE: Toast[] = []
let toasts = NONE
let nextId = 0
const listeners = new Set<() => void>()

function set(next: Toast[]) {
  toasts = next
  listeners.forEach((listener) => listener())
}

function dismiss(id: number) {
  set(toasts.filter((toast) => toast.id !== id))
}

/** Announce a short message (polite live region); it dismisses itself. */
export function toast(message: string, ms = 6_000) {
  const id = ++nextId
  set([...toasts, { id, message }])
  setTimeout(() => dismiss(id), ms)
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Mount once per shell. Always rendered so screen readers register the live region first. */
export function Toaster() {
  const items = useSyncExternalStore(
    subscribe,
    () => toasts,
    () => NONE,
  )

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 top-3 z-50 flex flex-col items-center gap-2 px-4"
    >
      {items.map((item) => (
        <div
          key={item.id}
          className="pointer-events-auto flex max-w-md items-center gap-3 rounded-md border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md"
        >
          <span>{item.message}</span>
          <button
            type="button"
            aria-label="Dismiss"
            className="-mr-1 inline-flex size-8 shrink-0 items-center justify-center rounded-md hover:bg-muted"
            onClick={() => dismiss(item.id)}
          >
            <X aria-hidden className="size-4" />
          </button>
        </div>
      ))}
    </div>
  )
}
