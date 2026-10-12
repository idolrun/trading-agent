"use client"

import { useState } from "react"
import { Dialog } from "@/components/dialog"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { TextField } from "@/features/auth/submit"
import { useSession } from "@/features/auth/use-session"
import { useUserMutations, useUsers, type UserWithPassword } from "@/features/users/api"
import { formatStamp } from "@/lib/format"

const when = (iso: string | null) => (iso ? formatStamp(iso) : "never")

function TemporaryPassword({ issued, onClose }: { issued: UserWithPassword | null; onClose: () => void }) {
  const [copiedFor, setCopiedFor] = useState<UserWithPassword | null>(null)
  const copied = issued !== null && copiedFor === issued
  return (
    <Dialog
      open={issued !== null}
      onOpenChange={(open) => !open && onClose()}
      title="Temporary password"
      description={
        issued
          ? `Give this to ${issued.user.email}. It is shown once and must be changed at first sign-in.`
          : "It is shown once and must be changed at first sign-in."
      }
    >
      <code className="rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm break-all select-all">
        {issued?.temporaryPassword}
      </code>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={async () => {
            if (!issued || !navigator.clipboard) return
            try {
              await navigator.clipboard.writeText(issued.temporaryPassword)
              setCopiedFor(issued)
            } catch {
              setCopiedFor(null)
            }
          }}
        >
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button type="button" onClick={onClose}>
          Done
        </Button>
      </div>
    </Dialog>
  )
}

export function UserManager() {
  const query = useUsers()
  const me = useSession().data?.email
  const { add, setActive, resetPassword } = useUserMutations()
  const [email, setEmail] = useState("")
  const [issued, setIssued] = useState<UserWithPassword | null>(null)
  const error = add.error ?? setActive.error ?? resetPassword.error

  return (
    <section className="flex max-w-3xl flex-col gap-3">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          add.mutate(email.trim(), {
            onSuccess: (created) => {
              setEmail("")
              setIssued(created)
            },
          })
        }}
      >
        <div className="min-w-64 flex-1">
          <TextField
            label="Add a user by email"
            type="email"
            required
            autoComplete="off"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <Button type="submit" className="h-9" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add user"}
        </Button>
      </form>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.length === 0}
        loadingLabel="Loading users"
        emptyTitle="No users"
        emptyLayout="line"
      >
        {(users) => (
          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {users.map((user) => (
              <li
                key={user.userId}
                className="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex min-w-0 flex-col gap-0.5 text-sm">
                  <span className="flex flex-wrap items-center gap-x-2 font-medium break-all">
                    {user.email}
                    {user.email === me ? <span className="text-xs font-normal text-muted-foreground">You</span> : null}
                    {!user.isActive ? <span className="text-xs font-normal text-muted-foreground">Inactive</span> : null}
                    {user.mustChangePassword ? (
                      <span className="text-xs font-normal text-warning-foreground">Temporary password</span>
                    ) : null}
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    Last sign-in {when(user.lastLoginAt)} · added {when(user.createdAt)}
                  </span>
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={resetPassword.isPending || user.email === me}
                    onClick={() => resetPassword.mutate(user.userId, { onSuccess: setIssued })}
                  >
                    Reset password
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className={user.isActive ? "text-destructive hover:bg-destructive/10 hover:text-destructive" : undefined}
                    disabled={setActive.isPending || (user.isActive && user.email === me)}
                    onClick={() => setActive.mutate({ id: user.userId, active: !user.isActive })}
                  >
                    {user.isActive ? "Deactivate" : "Reactivate"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <TemporaryPassword issued={issued} onClose={() => setIssued(null)} />
    </section>
  )
}
