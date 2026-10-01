"use client"

import { useState } from "react"
import { Dialog } from "@/components/dialog"
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
    <Dialog
      open={open}
      onOpenChange={(next) => !next && form.onCancel()}
      title="Confirm with your password"
      description="This action needs a fresh password check."
    >
      <StepUpForm {...form} />
    </Dialog>
  )
}
