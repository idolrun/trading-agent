import type { Page } from "@playwright/test"
import { cardOnDesk, expect, optionEntry, PROVIDER_A, test, type Driver } from "./support/driver"

// AC-14 / FR-14: "1000c" without an underlying is always a review card. The suggestion (MU, the
// provider's recent underlying trading near 1000) must be confirmed before Approve unlocks.

async function reviewCard(page: Page, driver: Driver) {
  // MU trades near 1000 today, with a 1000 call listed for Friday.
  await driver.market({
    fixtures: [{ underlying: "MU", price: "995", chains: { "2026-10-02": [["1000", "call", "4.90", "5.10"]] } }],
  })
  // The provider opened an MU position this week (the suggestion's input); that card is rejected.
  await driver.post("Bought MU 1000C fri @ 5.00", [optionEntry("MU", "1000", "1000C", "fri", "5.00")])
  const earlier = await driver.cardFor("entry", { state: "pending" })
  await cardOnDesk(page, PROVIDER_A, "entry", "MU").getByRole("button", { name: "Reject" }).click()
  await driver.waitFor(() => driver.card(earlier.id), (c) => c.state === "rejected", "the MU entry rejected")

  await driver.advance(30)
  await driver.post("1000c fri @ 5.00", [optionEntry(null, "1000", "1000c", "fri", "5.00")])
  const review = await driver.waitFor(
    () => driver.cards("pending"),
    (cards) => cards.find((c) => c.cardType === "review"),
    "the review card",
  )
  const card = cardOnDesk(page, PROVIDER_A, "entry") // no underlying in the title
  return { review, card, approve: card.getByRole("button", { name: "Approve" }) }
}

test('missing underlying "1000c": review card, approve disabled until confirmation', async ({ page, driver }) => {
  const { review, card, approve } = await reviewCard(page, driver)
  expect(review).toMatchObject({ action: "entry", underlying: null, version: 1 })
  expect(review.proposal).toMatchObject({ missingFields: ["underlying_confirmation"], suggestedUnderlying: "MU" })
  expect(review.proposal.reviewReasons).toContain("underlying_missing")

  await expect(card.getByRole("listitem").filter({ hasText: "Review" }).first()).toBeVisible()
  await expect(approve).toBeDisabled()
  const complete = card.getByRole("button", { name: "Complete review" })
  await expect(complete).toBeDisabled() // nothing is preselected
  expect((await driver.card(review.id)).approval).toBeNull()

  await card.getByLabel("I confirm the underlying is MU").check()
  await complete.click()
  const confirmed = await driver.waitFor(() => driver.card(review.id), (c) => (c.version === 2 ? c : null), "version 2")
  expect(confirmed).toMatchObject({ state: "pending", approval: null })
  expect(confirmed.proposal.missingFields).toEqual([])
  expect(confirmed.events.at(-1)).toMatchObject({ reasonCode: "review_completed" })
  expect(await driver.orders()).toEqual([]) // confirming is not approving
})

// Confirming re-plans the card on the confirmed underlying (contract, size, legs) and opens the
// provider position the pipeline skipped while the underlying was unknown.
test("confirmed underlying: the card becomes tradable on MU", async ({ page, driver }) => {
  const { review, card } = await reviewCard(page, driver)
  await card.getByLabel("I confirm the underlying is MU").check()
  await card.getByRole("button", { name: "Complete review" }).click()
  const confirmed = await driver.waitFor(() => driver.card(review.id), (c) => (c.version === 2 ? c : null), "version 2")
  expect(confirmed).toMatchObject({ underlying: "MU", cardType: "normal" })
  expect(confirmed.proposal.qty).not.toBeNull()
  // the card is now titled with its underlying
  const approve = cardOnDesk(page, PROVIDER_A, "entry", "MU").getByRole("button", { name: "Approve" })
  await expect(approve).toBeEnabled()
  await approve.click()
  const order = await driver.waitFor(
    () => driver.orders(),
    (orders) => orders.find((o) => o.cardId === review.id),
    "an order for the confirmed card",
  )
  expect(order.brokerSymbol).toContain("MU")
})
