import type { Schemas } from "@/lib/api/types"

export type DiscordStatus = Schemas["DiscordStatus"]
export type DiscordChannel = Schemas["DiscordChannel"]

export const STATE_LABEL: Record<DiscordStatus["state"], string> = {
  not_configured: "Not configured",
  connecting: "Connecting",
  connected: "Connected",
  login_failed: "Login failed",
}

/** A Discord snowflake, kept as a string (64-bit ids lose precision as numbers). */
export const isChannelId = (value: string) => /^\d{10,32}$/.test(value)
