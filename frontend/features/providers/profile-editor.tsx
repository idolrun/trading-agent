"use client"

import { Textarea } from "@/components/ui/textarea"

import { Input } from "@/components/ui/input"

import { useState, type FormEvent } from "react"
import { Dialog } from "@/components/dialog"
import { Button } from "@/components/ui/button"
import {
  PROFILE_LISTS,
  putProfile,
  useManualMutation,
  useProfile,
  type Profile,
  type ProfileList,
} from "@/features/manual/api"
import { Field, FormError } from "@/features/manual/fields"

const LABELS: Record<ProfileList, { label: string; hint: string }> = {
  aliases: { label: "Aliases", hint: "Other names this provider uses for tickers. One per line." },
  lexicon: { label: "Lexicon", hint: "Terms and shorthand they use. One per line." },
  habits: { label: "Habits", hint: "Posting habits worth knowing. One per line." },
}

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean)
const toText = (list: string[] | undefined) => (list ?? []).join("\n")

function ProfileForm({ providerId, profile, onClose }: { providerId: string; profile: Profile; onClose: () => void }) {
  const [text, setText] = useState(() => Object.fromEntries(PROFILE_LISTS.map((k) => [k, toText(profile[k])])) as Record<ProfileList, string>)
  const [reason, setReason] = useState("")
  const save = useManualMutation({
    call: () => {
      // Only lists that changed are sent; the rest are left alone by the server.
      const changed = PROFILE_LISTS.filter((k) => lines(text[k]).join("\n") !== toText(profile[k]))
      return putProfile(providerId, { reason: reason.trim(), ...Object.fromEntries(changed.map((k) => [k, lines(text[k])])) })
    },
    success: "Provider profile saved",
    onDone: onClose,
  })
  const valid = reason.trim() !== ""

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) save.mutate(undefined)
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      {PROFILE_LISTS.map((k) => (
        <Field key={k} label={LABELS[k].label} hint={LABELS[k].hint}>
          {(id) => <Textarea id={id} rows={4} value={text[k]} onChange={(e) => setText({ ...text, [k]: e.target.value })} />}
        </Field>
      ))}
      <Field label="Reason">{(id) => <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      <FormError error={save.error} />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={!valid || save.isPending}>
          {save.isPending ? "Saving…" : "Save profile"}
        </Button>
      </div>
    </form>
  )
}

function ProfileDialog({ providerId, onClose }: { providerId: string; onClose: () => void }) {
  const profile = useProfile(providerId)
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Provider profile"
      description={`Provider ${providerId}. Hints the parser uses for this provider's messages.`}
    >
      {profile.data ? <ProfileForm providerId={providerId} profile={profile.data} onClose={onClose} /> : null}
      {profile.isPending ? <p className="text-sm text-muted-foreground">Loading profile</p> : null}
      <FormError error={profile.error} />
    </Dialog>
  )
}

export function ProfileEditor({ providerId }: { providerId: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
        Provider profile
      </Button>
      {open ? <ProfileDialog providerId={providerId} onClose={() => setOpen(false)} /> : null}
    </>
  )
}
