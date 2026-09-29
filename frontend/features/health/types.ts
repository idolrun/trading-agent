import type { Schemas } from "@/lib/api/types"

export type HealthDetails = Schemas["HealthDetails"]
export type HealthWindow = HealthDetails["window"]

export const HEALTH_WINDOWS: readonly { value: HealthWindow; label: string }[] = [
  { value: "session", label: "Session" },
  { value: "day", label: "Day" },
  { value: "7d", label: "7 days" },
]
