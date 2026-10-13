import type { Draft, ScalarKey } from "@/features/config/types"

/**
 * Mirror of the backend `RuntimeConfig` constraints (`runtime_config/schema.py`), only to
 * explain a mistake before saving. The server stays the authority and still validates.
 * Keys are the control names the form uses (`ConfigField` `name`).
 */
type Kind = "int" | "pct" | "prob" | "positive"

const KIND: Record<ScalarKey, Kind> = {
  feed_silence_s: "int",
  stale_entry_s: "int",
  catch_up_backlog_n: "int",
  poison_retry_limit: "int",
  context_recent_messages: "int",
  entry_validity_s: "int",
  entry_cancel_after_s: "int",
  quote_max_age_s: "int",
  exit_card_timeout_s: "int",
  exit_reprice_interval_s: "int",
  cutoff_1_min_before_close: "int",
  cutoff_2_min_before_close: "int",
  max_open_positions: "int",
  missing_underlying_lookback_days: "int",
  entry_chase_pct: "pct",
  exit_reprice_step_pct: "pct",
  exit_floor_pct: "pct",
  identity_price_tolerance_pct: "pct",
  missing_underlying_proximity_pct: "pct",
  max_daily_loss_usd: "positive",
  max_total_exposure_usd: "positive",
  adjacent_strike_ratio: "positive",
  llm_monthly_budget_usd: "positive",
  t_chat: "prob",
  t_field: "prob",
  t_type: "prob",
  t_link: "prob",
}

/** Fields the backend does not allow to be null: blank is an error, not "not set". */
const REQUIRED: readonly ScalarKey[] = [
  "stale_entry_s",
  "poison_retry_limit",
  "context_recent_messages",
  "missing_underlying_proximity_pct",
  "t_chat",
  "t_field",
  "t_type",
  "t_link",
]

const NUMBER = /^\d+(\.\d+)?$/

/** `null` = fine. `kind` rules match the schema: int >0, pct (0,100], prob [0,1], positive >0. */
export function checkNumber(text: string, kind: Kind, required: boolean): string | null {
  const value = text.trim()
  if (value === "") return required ? "Required. Enter a value; the server does not accept an empty one." : null
  if (!NUMBER.test(value) || (kind === "int" && value.includes("."))) {
    return kind === "int" ? "Enter a whole number such as 30." : "Enter a plain number such as 12.5 (no units or signs)."
  }
  const number = Number(value)
  if (kind === "prob") return number > 1 ? "Must be between 0 and 1, for example 0.8." : null
  if (number <= 0) return "Must be greater than 0."
  if (kind === "pct" && number > 100) return "Must be at most 100 (this is a percent)."
  return null
}

const SNOWFLAKE = /^\d+$/
export const snowflakeError = (text: string) =>
  SNOWFLAKE.test(text) && /[1-9]/.test(text) ? null : "Enter a Discord id: digits only, greater than 0."

/** Problems by control name; empty object = nothing to fix. */
export function validateDraft(draft: Draft): Record<string, string> {
  const problems: Record<string, string> = {}
  const add = (name: string, message: string | null) => {
    if (message) problems[name] = message
  }
  for (const key of Object.keys(KIND) as ScalarKey[]) {
    add(key, checkNumber(draft[key], KIND[key], REQUIRED.includes(key)))
  }
  const c1 = Number(draft.cutoff_1_min_before_close)
  const c2 = Number(draft.cutoff_2_min_before_close)
  if (!problems.cutoff_1_min_before_close && !problems.cutoff_2_min_before_close && draft.cutoff_1_min_before_close && draft.cutoff_2_min_before_close && c1 <= c2) {
    problems.cutoff_1_min_before_close = "Cutoff 1 must be more minutes before close than Cutoff 2."
  }
  const { jev } = draft
  add("jev_timeout_ms", checkNumber(jev.timeout_ms, "int", true))
  add("jev_queue_timeout_ms", checkNumber(jev.queue_timeout_ms, "int", true))
  add("jev_budget_ms", checkNumber(jev.budget_ms, "int", true))
  if (!problems.jev_timeout_ms && !problems.jev_queue_timeout_ms && !problems.jev_budget_ms) {
    if (Number(jev.budget_ms) < Number(jev.queue_timeout_ms) + Number(jev.timeout_ms)) {
      problems.jev_budget_ms = "Step budget must be at least queue wait + request timeout."
    }
  }
  return problems
}
