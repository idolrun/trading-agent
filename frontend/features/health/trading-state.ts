import { useQuery } from "@tanstack/react-query"
import { configKeys, fetchConfig } from "@/features/config/queries"
import { TRADING_MODES } from "@/features/config/types"
import { useHealthDetails } from "@/features/health/queries"

export type TradingMode = (typeof TRADING_MODES)[number]

/**
 * The configured trading mode. The only place the API exposes it is the runtime config, so this
 * reads that document on its own key (the config editor keeps its own copy and never refetches).
 * `unknown` while loading or when the config cannot be read: the header never guesses a mode.
 */
export function useTradingMode(): TradingMode | "unknown" {
  const { data } = useQuery({
    queryKey: configKeys.detail("trading-mode"),
    queryFn: fetchConfig,
    select: (config) => String(config.document.trading_mode ?? ""),
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: 1,
  })
  return TRADING_MODES.find((mode) => mode === data) ?? "unknown"
}

export type EntryStatus = {
  state: "loading" | "allowed" | "blocked"
  /** Why, from the health flags. The backend sends booleans only, so the reason is the flag. */
  reasons: string[]
}

/** New entries are blocked by either the kill switch or the entries-blocked flag. */
export function useEntryStatus(): EntryStatus {
  const { data } = useHealthDetails("session")
  if (!data) return { state: "loading", reasons: [] }
  const reasons = [
    data.flags.kill_switch ? "Kill switch is on" : null,
    data.flags.entries_blocked ? "Blocked by the system" : null,
  ].filter((reason) => reason != null)
  return { state: reasons.length > 0 ? "blocked" : "allowed", reasons }
}
