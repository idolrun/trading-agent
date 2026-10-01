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
