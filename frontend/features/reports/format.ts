const UNAVAILABLE = "—"

function num(value: string | number | null | undefined): number | null {
  if (value == null) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** "$1,234.50". `null` is *unavailable* and never shows as $0.00. */
export function money(value: string | null | undefined): string {
  const n = num(value)
  return n == null ? UNAVAILABLE : USD.format(n)
}

export type Direction = "gain" | "loss" | "flat" | "unavailable"

export function direction(value: string | null | undefined): Direction {
  const n = num(value)
  if (n == null) return "unavailable"
  return n > 0 ? "gain" : n < 0 ? "loss" : "flat"
}

/** "+$150.00", "−$50.00", "$0.00". The sign is in the text, so colour is never the only cue. */
export function signedMoney(value: string | null | undefined): string {
  const n = num(value)
  if (n == null) return UNAVAILABLE
  if (n === 0) return USD.format(0)
  return `${n > 0 ? "+" : "−"}${USD.format(Math.abs(n))}`
}

export function percent(ratio: string | null | undefined, digits = 1): string {
  const n = num(ratio)
  return n == null ? UNAVAILABLE : `${(n * 100).toLocaleString("en-US", { maximumFractionDigits: digits })}%`
}

export function count(value: number | null | undefined): string {
  return value == null ? UNAVAILABLE : value.toLocaleString("en-US")
}

export function price(value: string | null | undefined): string {
  const n = num(value)
  return n == null ? UNAVAILABLE : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })
}

export function factor(value: string | null | undefined): string {
  const n = num(value)
  return n == null ? UNAVAILABLE : n.toLocaleString("en-US", { maximumFractionDigits: 2 })
}

/** Change against the previous window as "+3" / "−2" / "no change"; unavailable when either side is. */
export function countChange(current: number, previous: number | null): string | null {
  if (previous == null) return null
  const delta = current - previous
  return delta === 0 ? "no change" : `${delta > 0 ? "+" : "−"}${Math.abs(delta)}`
}

export function moneyChange(current: string | null, previous: string | null): string | null {
  const a = num(current)
  const b = num(previous)
  if (a == null || b == null) return null
  const delta = a - b
  return delta === 0 ? "no change" : signedMoney(String(delta))
}

export function duration(seconds: number | string | null | undefined): string {
  const s = num(seconds)
  if (s == null || s < 0) return UNAVAILABLE
  if (s < 60) return `${Math.round(s)} s`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 === 0 ? `${hours} h` : `${hours} h ${minutes % 60} min`
}

const STAMP = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
})

export function stamp(iso: string | null | undefined): string {
  if (!iso) return UNAVAILABLE
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? UNAVAILABLE : STAMP.format(d)
}

export function words(code: string): string {
  const text = code.replace(/_/g, " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** "2026-09-29" or "2026-09-29T10:00" bucket keys as a short axis label. */
export function bucketLabel(key: string): string {
  if (key.includes("T")) return key.slice(11)
  const [y, m, d] = key.split("-")
  if (!d) return `${y}-${m}`
  return `${m}/${d}`
}

/** The provider's Discord display name when known, else a short id tail. */
export function providerName(id: string, name?: string | null): string {
  if (name) return name
  return id.length > 6 ? `Provider …${id.slice(-4)}` : `Provider ${id}`
}

export { UNAVAILABLE }
