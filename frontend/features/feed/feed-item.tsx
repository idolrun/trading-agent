import Link from "next/link"
import { Badge } from "@/components/badge"
import { Card, CardContent } from "@/components/ui/card"
import { labelBadge } from "@/features/cards/labels"
import { words } from "@/features/cards/types"
import type { FeedItem as Item } from "@/features/feed/types"
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

export function FeedItem({ item }: { item: Item }) {
  const posted = new Date(item.postedAt)
  const muted = item.labels.includes("muted")

  return (
    <Card role="article" data-message-id={item.messageId} className={cn("gap-0 py-0", muted && "opacity-70")}>
      <CardContent className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 font-medium break-words">Provider {item.providerId}</p>
        <time
          dateTime={item.postedAt}
          title={`${ET.format(posted)} ET`}
          className="text-xs text-muted-foreground tabular-nums"
        >
          {LOCAL.format(posted)}
        </time>
      </div>
      {item.replyToId ? (
        <p className="text-xs text-muted-foreground break-all">Reply to message {item.replyToId}</p>
      ) : null}
      <p className={cn("text-sm break-words whitespace-pre-wrap", !item.text && "text-muted-foreground italic")}>
        {bodyText(item)}
      </p>
      <ul aria-label="Status and labels" className="flex flex-wrap gap-1.5">
        {EVENT_BADGE[item.eventType] ? (
          <Badge asChild>
            <li>{EVENT_BADGE[item.eventType]}</li>
          </Badge>
        ) : null}
        <Badge asChild>
          <li>{item.status ? words(item.status) : "Not queued"}</li>
        </Badge>
        {item.labels.map((label) => (
          <Badge key={label} asChild>
            <li>{labelBadge(label)}</li>
          </Badge>
        ))}
        {item.msgTypes.map((type) => (
          <Badge key={`type:${type}`} asChild>
            <li>{`Type: ${words(type)}`}</li>
          </Badge>
        ))}
      </ul>
      {item.cardIds.length > 0 ? (
        <ul aria-label="Cards from this message" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {item.cardIds.map((id) => (
            <li key={id}>
              <Link href={`/cards/${id}`} className="underline underline-offset-2">
                Card {id.slice(0, 8)}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      </CardContent>
    </Card>
  )
}
