import { approveWith, cardOnDesk, expect, followUp, lit, optionEntry, ORIGIN_HEADERS, PROVIDER_A, test, type Driver } from "./support/driver"

// AC-4 / 7.2: an entry card that times out, whose price leaves the band before approval, or that
// a newer provider message supersedes, ends expired / invalidated and places no order.
const GOOGL_357_5C = { underlying: "GOOGL", expiry: "2026-10-02", strike: "357.5", right: "call" }

async function pendingEntry(driver: Driver) {
  await driver.post("Bought GOOGL 357.5C 10/2 @ 0.71", [optionEntry("GOOGL", "357.5", "357.5C", "10/2", "0.71")])
  return driver.cardFor("entry", { state: "pending" })
}

async function expectNoOrders(driver: Driver) {
  expect(await driver.orders()).toEqual([])
  expect(await driver.lots()).toEqual([])
}

test("timeout: the card expires unanswered", async ({ page, driver }) => {
  const card = await pendingEntry(driver)
  const onDesk = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await expect(onDesk.getByText("Under 1 min left")).toBeVisible()

  await driver.advance(60) // valid for entry_validity_s = 60 from the post (T0)
  await driver.waitFor(() => driver.card(card.id), (c) => c.state === "expired", "the card expired")
  await expect(onDesk).toBeHidden()
  expect((await driver.card(card.id)).approval).toBeNull()
  await expectNoOrders(driver)

  await page.goto(`/cards/${card.id}`)
  await expect(page.getByText("Expired", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0)
})

test("price band: the ask moved past the chase limit before approval", async ({ page, driver }) => {
  const card = await pendingEntry(driver)
  await driver.market({ quotes: [{ contract: GOOGL_357_5C, bid: "0.78", ask: "0.80" }] }) // > 0.71 + 5 %
  const onDesk = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await approveWith(onDesk, 1) // revalidated at approval (AC-4)
  await expect(onDesk).toBeHidden()

  const after = await driver.waitFor(() => driver.card(card.id), (c) => (c.state === "invalidated" ? c : null), "invalidated")
  expect(after.approval).toMatchObject({ decision: "approve" }) // the user said yes; revalidation said no
  await expectNoOrders(driver)
})

test("superseded: a newer provider message on the position invalidates the entry", async ({ page, driver }) => {
  const card = await pendingEntry(driver)
  await driver.advance(3)
  await driver.post("stop 0.50", [followUp("stop_set", card.providerPositionId, { stop_level: lit("0.50") })])

  await driver.waitFor(() => driver.card(card.id), (c) => c.state === "invalidated", "the entry card invalidated")
  await expect(cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")).toBeHidden()
  const late = await page.request.post(`/api/v1/cards/${card.id}/decision`, {
    headers: ORIGIN_HEADERS,
    data: { decision: "approve", version: card.version },
  })
  expect(late.status()).toBe(409)
  await expectNoOrders(driver)
})
