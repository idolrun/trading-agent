"use client"

import { Input } from "@/components/ui/input"

import { useState, type FormEvent } from "react"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { FormError } from "@/features/manual/fields"
import { useChangeChannel } from "@/features/discord/mutations"
import { useDiscordStatus } from "@/features/discord/queries"
import { isChannelId, STATE_LABEL, type DiscordStatus } from "@/features/discord/types"

const STATE_STYLE: Record<DiscordStatus["state"], string> = {
  not_configured: "",
  connecting: "border-warning text-warning-foreground",
  connected: "border-primary text-primary",
  login_failed: "border-destructive text-destructive",
}

export function DiscordScreen() {
  const query = useDiscordStatus()
  const change = useChangeChannel()
  const [text, setText] = useState("")
  const value = text.trim()
  const invalid = value !== "" && !isChannelId(value)

  function add(event: FormEvent) {
    event.preventDefault()
    if (!isChannelId(value)) return
    change.mutate({ action: "add", channelId: value }, { onSuccess: () => setText("") })
  }

  return (
    <QueryState query={query} loadingLabel="Loading Discord status" emptyTitle="No status">
      {(status) => (
        <div className="flex flex-col gap-6">
          <section aria-label="Connection" className="flex flex-col gap-1">
            <p className="flex items-center gap-2">
              <Badge className={STATE_STYLE[status.state]}>{STATE_LABEL[status.state]}</Badge>
              {status.account ? <span className="text-sm">{status.account}</span> : null}
            </p>
            <p className="text-sm text-muted-foreground">
              {status.lastMessageAt ? `Last message ${new Date(status.lastMessageAt).toLocaleString()}` : "No messages received yet"}
            </p>
            {status.state === "not_configured" ? (
              <p className="text-sm text-muted-foreground">
                Set <code>DISCORD_TOKEN</code> in the backend <code>.env</code> and restart to start reading Discord.
              </p>
            ) : null}
            {status.state === "login_failed" ? (
              <p role="alert" className="text-sm text-destructive">
                Discord rejected the token. Replace <code>DISCORD_TOKEN</code> and restart the backend.
              </p>
            ) : null}
          </section>

          <section aria-label="Watched channels" className="flex flex-col gap-3">
            <h2 className="text-lg font-medium">Watched channels</h2>
            {status.channels.length === 0 ? (
              <p className="text-sm text-muted-foreground">No channels yet. Messages from channels not listed here are ignored.</p>
            ) : (
              <ul className="divide-y divide-border rounded-md border border-border">
                {status.channels.map((channel) => (
                  <li key={channel.id} className="flex items-center justify-between gap-3 px-3 py-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {channel.name ? `#${channel.name}` : "Unknown channel"}
                        {channel.guild ? <span className="font-normal text-muted-foreground"> · {channel.guild}</span> : null}
                      </p>
                      <p className="text-xs tabular-nums text-muted-foreground">
                        {channel.id}
                        {!channel.visible && status.state === "connected" ? " · not visible to this account" : ""}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={`Remove ${channel.id}`}
                      disabled={change.isPending}
                      onClick={() => change.mutate({ action: "remove", channelId: channel.id })}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            <form onSubmit={add} className="flex flex-col gap-2">
              <Label htmlFor="discord-channel-id" className="leading-normal select-text">
                Channel ID
              </Label>
              <div className="flex gap-2">
                <Input
                  id="discord-channel-id"
                  className="flex-1"
                  inputMode="numeric"
                  placeholder="Right-click a channel, Copy Channel ID"
                  value={text}
                  aria-invalid={invalid}
                  onChange={(event) => setText(event.target.value)}
                />
                <Button type="submit" className="h-11" disabled={!isChannelId(value) || change.isPending}>
                  Add channel
                </Button>
              </div>
              {invalid ? <p role="alert" className="text-sm text-destructive">A channel ID is 10 to 32 digits.</p> : null}
              <FormError error={change.error} />
            </form>
          </section>
        </div>
      )}
    </QueryState>
  )
}
