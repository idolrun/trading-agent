import { describe, expect, it } from "vitest"
import { expiryShortcuts, guessEntry } from "@/features/manual/guess"

const MON = new Date(2026, 9, 12) // Monday 2026-10-12

describe("guessEntry", () => {
  it("reads ticker, 0dte, strike, right and a decimal price", () => {
    expect(guessEntry("QQQ 0dte 748c @ .42 @here", MON)).toEqual({
      instrument: "option",
      underlying: "QQQ",
      expiry: "2026-10-12",
      strike: "748",
      right: "call",
      limitPrice: "0.42",
    })
  })

  it("reads a dated put and a cashtag", () => {
    expect(guessEntry("$spy 10/17 580 puts at 1.25", MON)).toMatchObject({
      underlying: "SPY",
      expiry: "2026-10-17",
      strike: "580",
      right: "put",
      limitPrice: "1.25",
    })
  })

  it("rolls a past month/day into next year", () => {
    expect(guessEntry("NVDA 1/16 200c", MON).expiry).toBe("2027-01-16")
  })

  it("fills nothing it cannot see plainly (prices without a decimal are ambiguous)", () => {
    expect(guessEntry("I'm still in though", MON)).toEqual({})
    expect(guessEntry("I see 300 on the 165c", MON).underlying).toBeUndefined()
    expect(guessEntry("META 755cs 50 -> 250 @here", MON)).toEqual({
      instrument: "option",
      underlying: "META",
      strike: "755",
      right: "call",
    })
  })
})

describe("expiryShortcuts", () => {
  it("gives today, this Friday and next Friday, without duplicates on a Friday", () => {
    expect(expiryShortcuts(MON).map((d) => d.value)).toEqual(["2026-10-12", "2026-10-16", "2026-10-23"])
    expect(expiryShortcuts(new Date(2026, 9, 16)).map((d) => d.label)).toEqual(["Today", "Next Fri"])
  })
})
