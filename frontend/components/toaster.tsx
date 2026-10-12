"use client"

import { toast as sonnerToast } from "sonner"
import { Toaster as SonnerToaster } from "@/components/ui/sonner"

/** Announce a short message. The same text replaces a toast that is still showing. */
export function toast(message: string, ms = 6_000) {
  sonnerToast(message, { duration: ms, id: message })
}

export function Toaster() {
  return <SonnerToaster position="top-center" />
}
