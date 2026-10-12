import { describe, expect, it } from "vitest"
import { blockedReason, cardGroup, isPastDeadline } from "@/features/cards/groups"
import type { Card } from "@/features/cards/types"
import { cardFixture } from "@/test/msw"

const card = (extra: Partial<Card> = {}, proposal: Partial<Card["proposal"]> = {}): Card => ({
  ...cardFixture,
  ...extra,
  proposal: { ...cardFixture.proposal, ...proposal },
})

describe("cardGroup", () => {
  it("a clean pending card needs a decision", () => expect(cardGroup(card(), false)).toBe("decide"))
  it("missing fields or a review card needs review", () => {
    expect(cardGroup(card({}, { missingFields: ["expiry"] }), false)).toBe("review")
    expect(cardGroup(card({ cardType: "review" }), false)).toBe("review")
  })
  it("disabled, or past its deadline, is blocked", () => {
    expect(cardGroup(card({ state: "disabled", cardType: "disabled" }), false)).toBe("blocked")
    expect(cardGroup(card({}, { disabledReason: "tier" }), false)).toBe("blocked")
    expect(cardGroup(card(), true)).toBe("blocked")
  })
  it("an expired deadline outranks missing fields: nothing can be completed", () => {
    expect(cardGroup(card({}, { missingFields: ["expiry"] }), true)).toBe("blocked")
  })
  it("decided and terminal cards are history", () => {
    for (const state of ["approved", "submitted", "rejected", "expired", "invalidated"] as const) {
      expect(cardGroup(card({ state }), false)).toBe("done")
    }
  })
  it("reads the deadline and explains a block", () => {
    expect(isPastDeadline(card({ validUntil: "2026-09-29T14:05:00Z" }), Date.parse("2026-09-29T14:06:00Z"))).toBe(true)
    expect(isPastDeadline(card({ validUntil: null }), Date.now())).toBe(false)
    expect(blockedReason(card({}, { disabledReason: "tier cannot buy" }), false)).toBe("tier cannot buy")
    expect(blockedReason(card(), true)).toMatch(/validity window/)
  })
})
