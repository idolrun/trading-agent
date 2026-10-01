import type { Schemas } from "@/lib/api/types"

export type ConfigResponse = Schemas["ConfigResponse"]
export type ConfigChange = Schemas["ConfigChangeOut"]
/**
 * The wire document (snake_case keys). Not `Schemas["RuntimeConfig"]`: the generated type
 * says snowflake lists are `number[]`, but they are strings here (64-bit ids).
 */
export type ConfigDoc = Record<string, unknown>

export const TRADING_MODES = ["shadow", "paper", "live"] as const

// Integer and decimal scalars. Drafts keep both as input strings; only `toDoc` converts.
export const INT_FIELDS = [
  "feed_silence_s",
  "stale_entry_s",
  "catch_up_backlog_n",
  "poison_retry_limit",
  "context_recent_messages",
  "entry_validity_s",
  "entry_cancel_after_s",
  "exit_card_timeout_s",
  "exit_reprice_interval_s",
  "cutoff_1_min_before_close",
  "cutoff_2_min_before_close",
  "max_open_positions",
  "missing_underlying_lookback_days",
] as const
export const DECIMAL_FIELDS = [
  "entry_chase_pct",
  "exit_reprice_step_pct",
  "exit_floor_pct",
  "max_daily_loss_usd",
  "max_total_exposure_usd",
  "identity_price_tolerance_pct",
  "adjacent_strike_ratio",
  "missing_underlying_proximity_pct",
  "t_chat",
  "t_field",
  "t_type",
  "t_link",
  "llm_monthly_budget_usd",
] as const
export type ScalarKey = (typeof INT_FIELDS)[number] | (typeof DECIMAL_FIELDS)[number]
const SCALARS: readonly ScalarKey[] = [...INT_FIELDS, ...DECIMAL_FIELDS]
const isInt = (key: ScalarKey) => (INT_FIELDS as readonly string[]).includes(key)

export type Row = { name: string; value: string }
export type RowsKey = "option_tiers" | "stock_tiers" | "symbol_map"
export type ListKey = "channels" | "muted_authors"

export type Draft = Record<ScalarKey, string> &
  Record<RowsKey, Row[]> &
  Record<ListKey, string[]> & {
    trading_mode: string
    jev: { model: string; timeout_ms: string; enabled: boolean }
    llm: string // JSON text
    note_guard_enabled: boolean
  }

const OPTION_TIER_NAMES = ["default", "small", "lotto"]

/** RuntimeConfig defaults (backend `runtime_config/schema.py`), for a config that was never saved. */
export function defaultDocument(): ConfigDoc {
  const doc: ConfigDoc = Object.fromEntries(SCALARS.map((key) => [key, null]))
  return {
    trading_mode: "shadow",
    channels: [],
    muted_authors: [],
    ...doc,
    stale_entry_s: 30,
    poison_retry_limit: 3,
    missing_underlying_proximity_pct: "10",
    t_chat: "0.9",
    t_field: "0.8",
    t_type: "0.8",
    t_link: "0.8",
    option_tiers: null,
    stock_tiers: null,
    symbol_map: { SPX: "SPXW" },
    jev: { model: null, timeout_ms: 300, enabled: false },
    llm: {
      roles: {
        extract: { deadline_ms: 3000, per_attempt_cap_ms: 2000, chain: [] },
        note: { deadline_ms: 4000, per_attempt_cap_ms: null, chain: [] },
        prelabel: { deadline_ms: null, per_attempt_cap_ms: null, chain: [] },
      },
      prices: {},
      params: {},
      concurrency: {},
    },
    note_guard_enabled: true,
  }
}

/** Same rule as the server: every unset (null) field blocks entries. */
export function missingKeys(doc: ConfigDoc): string[] {
  return Object.keys(doc).filter((key) => doc[key] === null)
}

const toRows = (record: unknown, fallback: string[] = []): Row[] =>
  record && typeof record === "object"
    ? Object.entries(record).map(([name, value]) => ({ name, value: String(value) }))
    : fallback.map((name) => ({ name, value: "" }))

export function toDraft(doc: ConfigDoc): Draft {
  const jev = (doc.jev ?? {}) as { model?: string | null; timeout_ms?: number | null; enabled?: boolean }
  const scalars = Object.fromEntries(SCALARS.map((key) => [key, doc[key] == null ? "" : String(doc[key])]))
  return {
    ...(scalars as Record<ScalarKey, string>),
    trading_mode: String(doc.trading_mode ?? "shadow"),
    channels: ((doc.channels as unknown[]) ?? []).map(String),
    muted_authors: ((doc.muted_authors as unknown[]) ?? []).map(String),
    option_tiers: toRows(doc.option_tiers, OPTION_TIER_NAMES),
    stock_tiers: toRows(doc.stock_tiers),
    symbol_map: toRows(doc.symbol_map),
    jev: { model: jev.model ?? "", timeout_ms: jev.timeout_ms == null ? "" : String(jev.timeout_ms), enabled: !!jev.enabled },
    llm: JSON.stringify(doc.llm ?? {}, null, 2),
    note_guard_enabled: doc.note_guard_enabled !== false,
  }
}

const orNull = (text: string) => (text.trim() === "" ? null : text.trim())
const toNumber = (text: string) => (text.trim() === "" ? null : Number(text))

/**
 * Blank rows are dropped (nullable tiers: any row without a value, so the prefilled tier names
 * do not count); no rows at all is `null` (tiers) or `{}` (symbol map).
 */
function fromRows(rows: Row[], nullable: boolean) {
  const kept = rows.filter((row) => row.value.trim() !== "" || (!nullable && row.name.trim() !== ""))
  return kept.length === 0 && nullable ? null : Object.fromEntries(kept.map((row) => [row.name.trim(), row.value.trim()]))
}

/** Text that is not JSON is passed through as a string; the screen blocks saving before that reaches the wire. */
export function llmError(text: string): string | null {
  try {
    JSON.parse(text)
    return null
  } catch (error) {
    return `Not valid JSON: ${(error as Error).message}`
  }
}

export function toDoc(draft: Draft): ConfigDoc {
  const scalars = Object.fromEntries(
    SCALARS.map((key) => [key, isInt(key) ? toNumber(draft[key]) : orNull(draft[key])]),
  )
  return {
    ...scalars,
    trading_mode: draft.trading_mode,
    channels: draft.channels,
    muted_authors: draft.muted_authors,
    option_tiers: fromRows(draft.option_tiers, true),
    stock_tiers: fromRows(draft.stock_tiers, true),
    symbol_map: fromRows(draft.symbol_map, false),
    jev: { model: orNull(draft.jev.model), timeout_ms: toNumber(draft.jev.timeout_ms), enabled: draft.jev.enabled },
    llm: llmError(draft.llm) ? draft.llm : JSON.parse(draft.llm),
    note_guard_enabled: draft.note_guard_enabled,
  }
}

/** Key-order-independent JSON, so equal documents compare equal however the server ordered them. */
export function canon(value: unknown): string {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : item,
  )
}

/** The document as the form would send it, so server and local documents share one shape. */
export const normalize = (doc: ConfigDoc): ConfigDoc => toDoc(toDraft(doc))

export function changedKeys(before: ConfigDoc, after: ConfigDoc): string[] {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(
    (key) => canon(before[key]) !== canon(after[key]),
  )
}

export type ServerErrors = { fields: Record<string, string[]>; general: string[] }
export const NO_ERRORS: ServerErrors = { fields: {}, general: [] }

/**
 * 422 `message` is one string, parts joined by "; ": `body.document.<field>[.<nested>]: <text>`.
 * Parts under a document field go to that top-level field (nested path kept); the rest are general.
 */
export function parseServerErrors(message: string): ServerErrors {
  const errors: ServerErrors = { fields: {}, general: [] }
  for (const part of message.split("; ")) {
    const match = /^body\.document\.([^:\s]+):\s*(.*)$/.exec(part)
    if (!match) {
      errors.general.push(part)
      continue
    }
    const [key, ...rest] = match[1].split(".")
    ;(errors.fields[key] ??= []).push(rest.length ? `${rest.join(".")}: ${match[2]}` : match[2])
  }
  return errors
}
