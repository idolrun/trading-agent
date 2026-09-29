import type { ListFilters, Schemas } from "@/lib/api/types"

export type FeedItem = Schemas["FeedItem"]
export type FeedPage = Schemas["Page_FeedItem_"]
export type FeedLabel = NonNullable<ListFilters<"feed_api_v1_feed_get">["label"]>
/** `providerId` is a Discord id: kept a string end to end (the generated type says number). */
export type FeedFilters = { providerId?: string; label?: FeedLabel }

/** The labels the server can filter on. Items may carry more (`held_until_open`), shown but not filterable. */
export const FEED_LABELS: FeedLabel[] = [
  "stale",
  "late",
  "backfilled",
  "muted",
  "channel_not_configured",
  "failed",
]

export const feedItemKey = (item: FeedItem) => `${item.messageId}:${item.eventType}:${item.eventSeq}`
