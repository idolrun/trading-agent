import { describe, expect, it } from "vitest"
import { formatDuration, formatStamp } from "@/lib/format"

describe("formatDuration", () => {
  it.each([
    [3.935191, "3.9 s"],
    [42.4, "42 s"],
    [252, "4 min 12 s"],
    [300, "5 min"],
    [7500, "2 h 5 min"],
    [-1, "—"],
    [Number.NaN, "—"],
  ])("%s -> %s", (seconds, text) => expect(formatDuration(seconds)).toBe(text))
})

describe("formatStamp", () => {
  it("shows the time for today and the date for another day", () => {
    const now = new Date("2026-09-29T15:00:00")
    expect(formatStamp("2026-09-29T14:05:09", now)).toMatch(/2:05:09/)
    expect(formatStamp("2026-09-28T14:05:09", now)).toMatch(/Sep 28/)
    expect(formatStamp("nope", now)).toBe("—")
  })
})
