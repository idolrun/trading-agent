/** Prices are decimal strings on the wire; parsed here for display only. */
export function formatPrice(value: string | number | null): string {
  if (value == null) return "-"
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatContract(contract: {
  underlying: string
  instrument: string
  right: string | null
  strike: string | null
  expiry: string | null
}): string {
  if (contract.instrument !== "option") return contract.underlying
  return [contract.underlying, contract.expiry, contract.strike, contract.right]
    .filter(Boolean)
    .join(" ")
}

const LOCAL_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" })

export function formatTime(iso: string): string {
  return LOCAL_TIME.format(new Date(iso))
}

/** Seconds as "3.9 s", "4 min 12 s" or "2 h 5 min": a readable age, never "3.935191s". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—"
  if (seconds < 10) return `${seconds.toFixed(1)} s`
  const whole = Math.round(seconds)
  if (whole < 60) return `${whole} s`
  const minutes = Math.floor(whole / 60)
  if (minutes < 60) return whole % 60 === 0 ? `${minutes} min` : `${minutes} min ${whole % 60} s`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 === 0 ? `${hours} h` : `${hours} h ${minutes % 60} min`
}

const SHORT_TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })
const SHORT_DATE_TIME = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
})

/** "2:05:09 PM" for today, "Sep 29, 2:05 PM" otherwise: a compact stamp for lists. */
export function formatStamp(iso: string, now = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toDateString() === now.toDateString() ? SHORT_TIME.format(date) : SHORT_DATE_TIME.format(date)
}

/** The provider's Discord display name when the backend has seen one, else a short id tail ("Provider …2652"). */
export function providerLabel(id: string, name?: string | null): string {
  if (name) return name
  return id.length > 6 ? `Provider …${id.slice(-4)}` : `Provider ${id}`
}
