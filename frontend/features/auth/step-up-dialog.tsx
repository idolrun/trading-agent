"use client"

import { Dialog } from "radix-ui"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { ErrorAlert, Spinner, TextField, type SubmitState } from "@/features/auth/submit"

type Props = {
  state: SubmitState
  onConfirm: (password: string) => void
  onCancel: () => void
}

// Lives inside Dialog.Content, which unmounts on close: the typed password never lingers.
function StepUpForm({ state, onConfirm, onCancel }: Props) {
  const [password, setPassword] = useState("")
  const pending = state.phase === "pending"
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        onConfirm(password)
      }}
    >
      <div>
        <Dialog.Title className="text-lg font-semibold">Confirm with your password</Dialog.Title>
        <Dialog.Description className="text-sm text-muted-foreground">
          This action needs a fresh password check.
        </Dialog.Description>
      </div>
      <TextField
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        autoFocus
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <ErrorAlert state={state} />
      {pending ? <Spinner label="Checking your password" /> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {state.phase === "failed" ? "Try again" : "Confirm"}
        </Button>
      </div>
    </form>
  )
}

export function StepUpDialog({ open, ...form }: Props & { open: boolean }) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && form.onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-background p-5 shadow-lg">
          <StepUpForm {...form} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
