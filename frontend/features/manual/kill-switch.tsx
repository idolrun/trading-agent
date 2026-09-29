"use client"

import { useState, type FormEvent } from "react"
import { Dialog } from "@/components/dialog"
import { Button } from "@/components/ui/button"
import { useHealthDetails } from "@/features/health/queries"
import { postKillSwitch, useManualMutation } from "@/features/manual/api"
import { Field, FormError, inputClass } from "@/features/manual/fields"

function KillSwitchDialog({ turnOn, onClose }: { turnOn: boolean; onClose: () => void }) {
  const [reason, setReason] = useState("")
  const toggle = useManualMutation({
    call: (text: string) => postKillSwitch({ active: turnOn, reason: text }),
    success: turnOn ? "Kill switch is on" : "Kill switch is off",
    onDone: onClose,
  })
  const valid = reason.trim().length > 0

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) toggle.mutate(reason.trim())
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={turnOn ? "Turn kill switch on" : "Turn kill switch off"}
      description={
        turnOn
          ? "New entries are blocked until you turn it off. Existing lots and stops are not touched."
          : "New entries are allowed again."
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field label="Reason">{(id) => <input id={id} value={reason} onChange={(e) => setReason(e.target.value)} className={inputClass} />}</Field>
        <FormError error={toggle.error} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant={turnOn ? "destructive" : "default"} disabled={!valid || toggle.isPending}>
            {toggle.isPending ? "Saving…" : turnOn ? "Turn on" : "Turn off"}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Header toggle. State comes from the health flags, so it also follows other sessions and `flag.changed`. */
export function KillSwitch() {
  const health = useHealthDetails("session")
  const [open, setOpen] = useState(false)
  const active = health.data?.flags.kill_switch === true

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant={active ? "default" : "destructive"}
        disabled={!health.data}
        onClick={() => setOpen(true)}
      >
        {active ? "Kill switch on: turn off" : "Kill switch"}
      </Button>
      {open ? <KillSwitchDialog turnOn={!active} onClose={() => setOpen(false)} /> : null}
    </>
  )
}
