/** The manual entry form's fields, as typed (strings until submit). */
export type EntryDraft = {
  instrument: "option" | "stock"
  underlying: string
  expiry: string
  strike: string
  right: "call" | "put"
  qty: string
  limitPrice: string
}

export const EMPTY_DRAFT: EntryDraft = {
  instrument: "option",
  underlying: "",
  expiry: "",
  strike: "",
  right: "call",
  qty: "",
  limitPrice: "",
}

/** `YYYY-MM-DD` of a local date (what `<input type="date">` holds). */
export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Today, this week's Friday and next week's Friday (an expiry is usually one of them). */
export function expiryShortcuts(today: Date): { label: string; value: string }[] {
  const friday = new Date(today)
  friday.setDate(today.getDate() + ((5 - today.getDay() + 7) % 7))
  const next = new Date(friday)
  next.setDate(friday.getDate() + 7)
  const days = [
    { label: "Today", value: isoDay(today) },
    { label: "Fri", value: isoDay(friday) },
    { label: "Next Fri", value: isoDay(next) },
  ]
  return days.filter((day, i) => days.findIndex((other) => other.value === day.value) === i)
}

const STRIKE = /\b(\d+(?:\.\d+)?)\s?([cp])(?:alls?|uts?|s)?\b/i
// Two letters or more: a lone "I"/"A" is a word. One-letter tickers still come in as a $cashtag.
const TICKER_BEFORE_STRIKE = /\$?\b([A-Z]{2,5})\b(?=[^\n]{0,24}?\b\d+(?:\.\d+)?\s?[cCpP](?:alls?|uts?|s)?\b)/
const CASHTAG = /\$([A-Za-z]{1,5})\b/
const DATE = /\b(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?\b/
const PRICE = /(?:@|\bat\b)\s*(\d*\.\d+)/i

/**
 * What a signal's text says plainly: "QQQ 0dte 748c @ .42", "$SPY 10/17 580 calls". Only
 * unambiguous fields are filled; the user sees and confirms every one before submitting.
 * ponytail: a few regexes for the common shapes, not a parser; the backend LLM is the real reader.
 */
export function guessEntry(text: string, today: Date): Partial<EntryDraft> {
  const out: Partial<EntryDraft> = {}
  const strike = STRIKE.exec(text)
  if (strike) {
    out.instrument = "option"
    out.strike = strike[1]
    out.right = strike[2].toLowerCase() === "p" ? "put" : "call"
  }
  const ticker = (strike ? TICKER_BEFORE_STRIKE.exec(text) : null) ?? CASHTAG.exec(text)
  if (ticker) out.underlying = ticker[1].toUpperCase()
  if (/\b0\s?dte\b/i.test(text)) out.expiry = isoDay(today)
  else {
    const date = DATE.exec(text)
    const month = date ? Number(date[1]) : 0
    const day = date ? Number(date[2]) : 0
    if (date && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      let year = date[3] ? Number(date[3].length === 2 ? `20${date[3]}` : date[3]) : today.getFullYear()
      const candidate = new Date(year, month - 1, day)
      if (!date[3] && isoDay(candidate) < isoDay(today)) year += 1 // "1/16" in October is next year
      out.expiry = isoDay(new Date(year, month - 1, day))
    }
  }
  const price = PRICE.exec(text)
  if (price) out.limitPrice = price[1].startsWith(".") ? `0${price[1]}` : price[1]
  return out
}
