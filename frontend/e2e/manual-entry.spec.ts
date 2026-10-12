import { expect, test } from "./support/driver"

// 7.9: a manual entry places one limit order with no password prompt (step-up removed 2026-10-12),
// under its own approved card, revalidated like a signal order (shadow mode: filled at the ask).
test("manual entry: submit places the order", async ({ page, driver }) => {
  await page.goto("/manual/entry")
  const form = page.getByRole("form", { name: "Manual entry" })
  await form.getByLabel("Underlying").fill("googl")
  await form.getByLabel("Expiry").fill("2026-10-02")
  await form.getByLabel("Strike").fill("357.5")
  await form.getByRole("radio", { name: "Call" }).click()
  await form.getByLabel("Quantity").fill("1")
  await form.getByLabel("Limit price").fill("0.71")
  await form.getByRole("button", { name: "Submit entry" }).click()

  await expect(page.getByText("Entry submitted")).toBeVisible()

  const [card] = await driver.cards()
  expect(card).toMatchObject({ action: "entry", providerId: null, providerPositionId: null })
  const detail = await driver.card(card.id)
  expect(detail.approval).toMatchObject({ decision: "approve", stepUp: false })
  const [order] = await driver.orders()
  expect(order).toMatchObject({
    cardId: card.id,
    kind: "entry",
    side: "buy",
    qty: 1,
    limitPrice: "0.71",
    mode: "shadow",
  })
  const lot = await driver.lotOf(null, "GOOGL")
  expect(lot).toMatchObject({ qty: 1, isForeign: true, contract: { strike: "357.5", right: "call" } })
})
