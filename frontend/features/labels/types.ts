import type { Schemas } from "@/lib/api/types"

export type QueueItem = Schemas["QueueItem"]
export type QueuePage = Schemas["Page_QueueItem_"]
export type Label = Schemas["Label"]
export type LabelSignal = Schemas["LabelSignal"]
export type LabelLink = Schemas["LabelLink"]
export type LabelDetail = Schemas["LabelDetail"]
export type LabelProgress = Schemas["LabelProgress"]
export type MsgType = Schemas["MsgType"]
/** `providerId` is a Discord id: kept a string end to end (the generated type says number). */
export type QueueFilters = { providerId?: string }

export const MSG_TYPES: MsgType[] = [
  "entry", "add", "trim", "full_exit", "soft_exit", "stop_set", "stop_move", "stop_cancel", "tp_set",
  "tp_hit_notice", "correction", "watch", "conditional", "commentary_pnl", "stock_entry", "stock_exit",
  "out_of_scope_strategy", "chatter", "unparsed",
]

export const INSTRUMENTS = ["option", "stock", "unclear"] as const

/** `SignalFields` names the API accepts (unknown names are a 422). */
export const FIELD_NAMES = [
  "underlying", "expiry_token", "strike", "right", "entry_price", "entry_price_low", "entry_price_high",
  "price_unit_hint", "size_tier", "qty_stated", "stop_level", "stop_basis", "candle_close", "tp_levels",
  "exit_fraction", "breakeven_stop", "stop_removed", "is_add", "prose_condition", "correction_target_message_id",
]

export const BOOLEAN_FIELDS = ["candle_close", "breakeven_stop", "stop_removed", "is_add"]
export const ENUM_FIELDS: Record<string, string[]> = {
  right: ["call", "put"],
  price_unit_hint: ["per_share", "per_contract"],
  stop_basis: ["premium", "underlying"],
  exit_fraction: ["all", "half", "third", "quarter", "other_stated", "not_stated"],
}

export const emptyValue = (name: string): unknown =>
  BOOLEAN_FIELDS.includes(name) ? false : name === "tp_levels" ? [] : (ENUM_FIELDS[name]?.[0] ?? "")

/** Wire form of an edited field: no blanks, whole-number quantity. */
export function cleanFields(fields: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(fields)) {
    if (Array.isArray(value)) {
      const items = value.map(String).map((v) => v.trim()).filter(Boolean)
      if (items.length > 0) out[name] = items
    } else if (typeof value === "string") {
      const text = value.trim()
      if (text !== "") out[name] = name === "qty_stated" && Number.isFinite(Number(text)) ? Number(text) : text
    } else if (value != null) out[name] = value
  }
  return out
}

export const cleanLabel = (signals: LabelSignal[], link: LabelLink | null): Label => ({
  signals: signals.map((signal) => ({ ...signal, fields: cleanFields(signal.fields) })),
  link,
})

/** Drop signal `at`, renumber the rest; a link on it goes, a link after it shifts down. */
export function removeSignal(signals: LabelSignal[], link: LabelLink | null, at: number) {
  return {
    signals: signals.filter((_, index) => index !== at).map((signal, index) => ({ ...signal, idx: index })),
    link: link == null || link.idx === at ? null : { ...link, idx: link.idx > at ? link.idx - 1 : link.idx },
  }
}

/**
 * Where each field value sits in the message (Unicode code points, like the API). Values that
 * are not found verbatim are skipped.
 */
export function fieldSpans(text: string, signals: LabelSignal[]): Schemas["SpanOut"][] {
  const lower = text.toLowerCase()
  const fold = lower.length === text.length // case-insensitive only when offsets survive lowercasing
  const spans: Schemas["SpanOut"][] = []
  for (const { fields } of signals) {
    for (const [field, value] of Object.entries(fields)) {
      for (const part of Array.isArray(value) ? value : [value]) {
        if (typeof part !== "string" && typeof part !== "number") continue
        const needle = String(part).trim()
        if (needle === "") continue
        const at = fold ? lower.indexOf(needle.toLowerCase()) : text.indexOf(needle)
        if (at < 0) continue
        const start = Array.from(text.slice(0, at)).length
        spans.push({ field, start, end: start + Array.from(needle).length })
      }
    }
  }
  return spans
}

/** True when a key press is going into a control (or a button, for Enter, which activates it). */
export function keyIgnored(event: KeyboardEvent): boolean {
  if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return true
  const target = event.target as HTMLElement
  const controls =
    "input:not([type=radio]):not([type=checkbox]), textarea, select, [contenteditable='true']"
  return target.closest(event.key === "Enter" ? `${controls}, button, a, input` : controls) != null
}
