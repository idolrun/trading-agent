"use client"

import { toast as sonnerToast } from "sonner"
import { Toaster as SonnerToaster } from "@/components/ui/sonner"

/** Announce a short message. Same call shape as before; Sonner dismisses it. */
export function toast(message: string, ms = 6_000) {
  sonnerToast(message, { duration: ms })
}

export function Toaster() {
  return <SonnerToaster position="top-center" />
}
