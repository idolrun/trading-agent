import { words } from "@/features/cards/types"

/** Short titles for kinds the desk actually raises. Unknown kinds stay readable via `words`. */
const TITLES: Record<string, string> = {
  feed_silence: "Feed silent",
  feed_silent: "Feed silent",
  poison: "Unprocessable message",
  reconcile_mismatch: "Reconciliation mismatch",
  catch_up: "Feed catching up",
  system_flag: "Safety flag",
  message_api: "Message source unavailable",
  invariant_block: "Safety check blocked an order",
  entry_partial: "Entry partly filled",
  eod_forced_close: "Expiring position closed",
  llm_spend: "Model spend",
}

export function alertTitle(kind: string): string {
  return TITLES[kind] ?? words(kind)
}
