"use client"

import { Dialog } from "radix-ui"
import { Button } from "@/components/ui/button"
import { CeremonyAlert, Spinner, type CeremonyState } from "@/features/auth/ceremony"

export function StepUpDialog({
  open,
  state,
  onConfirm,
  onCancel,
}: {
  open: boolean
  state: CeremonyState
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 flex w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-lg border border-border bg-background p-5 shadow-lg">
          <div>
            <Dialog.Title className="text-lg font-semibold">Confirm with your passkey</Dialog.Title>
            <Dialog.Description className="text-sm text-muted-foreground">
              This action needs a fresh passkey check.
            </Dialog.Description>
          </div>
          <CeremonyAlert state={state} />
          {state.phase === "pending" ? <Spinner label="Waiting for your passkey" /> : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="button" onClick={onConfirm} disabled={state.phase === "pending"}>
              {state.phase === "failed" ? "Try again" : "Confirm with passkey"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
