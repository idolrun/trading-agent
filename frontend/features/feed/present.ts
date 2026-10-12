import { words } from "@/features/cards/types"

/** Inbox status, as a handling outcome. `done` means the message was interpreted, not that an order filled. */
const STATUS: Record<string, string> = {
  received: "Received",
  processing: "Processing",
  done: "Interpreted",
  failed: "Failed",
  stale: "Stale",
}

export function feedStatusLabel(status: string | null): string {
  if (!status) return "Not queued"
  return STATUS[status] ?? words(status)
}

export function feedTypeLabel(type: string): string {
  return `Interpreted as ${words(type)}`
}
