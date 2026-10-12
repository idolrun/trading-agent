import type { FeedItem } from "@/features/feed/types"

/** Client filters over messages already loaded. Provider and label stay server-side. */
export type FeedView = {
  text: string
  channelId: string
  /** `status:<value>` or `type:<msgType>`. Empty means all. */
  classification: string
  problems: boolean
  hasCard: boolean
}

export const emptyFeedView: FeedView = {
  text: "",
  channelId: "",
  classification: "",
  problems: false,
  hasCard: false,
}

const PROBLEM_LABELS = new Set(["stale", "late", "failed"])

export function matchesFeed(item: FeedItem, view: FeedView): boolean {
  const query = view.text.trim().toLowerCase()
  if (query && !(item.text ?? "").toLowerCase().includes(query)) return false
  if (view.channelId && item.channelId !== view.channelId) return false
  if (view.hasCard && item.cardIds.length === 0) return false
  if (view.problems) {
    const hit =
      item.status === "failed" || item.status === "stale" || item.labels.some((label) => PROBLEM_LABELS.has(label))
    if (!hit) return false
  }
  if (view.classification.startsWith("status:")) {
    const status = view.classification.slice("status:".length)
    if ((item.status ?? "none") !== status) return false
  } else if (view.classification.startsWith("type:")) {
    const type = view.classification.slice("type:".length)
    if (!item.msgTypes.includes(type)) return false
  }
  return true
}
