"use client"

import { ChevronDown } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { labelBadge } from "@/features/cards/labels"
import { words } from "@/features/cards/types"
import { feedStatusLabel, feedTypeLabel } from "@/features/feed/present"
import type { FeedItem as Item } from "@/features/feed/types"
import { providerLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

const ET = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  dateStyle: "medium",
  timeStyle: "medium",
})
const LOCAL = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" })

const EVENT_BADGE: Record<string, string> = { edit: "Edited", delete: "Deleted" }

function bodyText(item: Item): string {
  if (item.text) return item.text
  return item.eventType === "delete" ? "(deleted, no text)" : "(no text)"
}

export function FeedItem({ item, channelName }: { item: Item; channelName?: string | null }) {
  const [open, setOpen] = useState(false)
  const posted = new Date(item.postedAt)
  const muted = item.labels.includes("muted")
  const who = providerLabel(item.providerId)
  const where = channelName || (item.channelId.length > 6 ? `Channel …${item.channelId.slice(-4)}` : `Channel ${item.channelId}`)

  return (
    <article
      data-message-id={item.messageId}
      className={cn("border-b border-border py-2.5", muted && "opacity-70")}
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 text-sm">
          <span className="font-medium">{where}</span>
          <span className="text-muted-foreground"> · </span>
          <span>{who}</span>
        </p>
        <time
          dateTime={item.postedAt}
          title={`${ET.format(posted)} ET`}
          className="shrink-0 text-xs text-muted-foreground tabular-nums"
        >
          {LOCAL.format(posted)}
        </time>
      </div>
      {item.replyToId ? (
        <p className="mt-1 text-xs text-muted-foreground break-all">Reply to message {item.replyToId}</p>
      ) : null}
      <p className={cn("mt-1 text-sm break-words whitespace-pre-wrap", !item.text && "text-muted-foreground italic")}>
        {bodyText(item)}
      </p>
      <ul aria-label="Status and labels" className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
        {EVENT_BADGE[item.eventType] ? <li className="font-medium text-foreground">{EVENT_BADGE[item.eventType]}</li> : null}
        <li>{feedStatusLabel(item.status)}</li>
        {item.labels.map((label) => (
          <li key={label}>{labelBadge(label)}</li>
        ))}
        {item.msgTypes.map((type) => (
          <li key={`type:${type}`}>{feedTypeLabel(type)}</li>
        ))}
      </ul>
      {item.cardIds.length > 0 ? (
        <ul aria-label="Cards from this message" className="mt-1 flex flex-wrap gap-x-3 text-sm">
          {item.cardIds.map((id) => (
            <li key={id}>
              <Link href={`/cards/${id}`} className="underline underline-offset-2">
                Card {id.slice(0, 8)}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="mt-1 inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? "Hide details" : "Message details"}
        <ChevronDown aria-hidden className={cn("size-3.5", open && "rotate-180")} />
      </button>
      {open ? (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-l border-border pl-3 text-xs text-muted-foreground">
          <dt>Provider ID</dt>
          <dd className="font-mono break-all text-foreground">{item.providerId}</dd>
          <dt>Channel ID</dt>
          <dd className="font-mono break-all text-foreground">{item.channelId}</dd>
          <dt>Message ID</dt>
          <dd className="font-mono break-all text-foreground">{item.messageId}</dd>
          <dt>Event</dt>
          <dd className="text-foreground">
            {words(item.eventType)} · seq {item.eventSeq}
          </dd>
          <dt>Inbox status</dt>
          <dd className="text-foreground">{item.status ?? "not queued"}</dd>
          <dt>Posted</dt>
          <dd className="text-foreground">{ET.format(posted)} ET</dd>
        </dl>
      ) : null}
      {open ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Received, interpreted, and card created are separate from approval and fills. This row does not show an order.
        </p>
      ) : null}
    </article>
  )
}
