import { cardOnDesk, expect, optionEntry, PROVIDER_A, test } from "./support/driver"

// AC-5: an edit before the trade replaces the card with a new one that needs its own approval; a
// correction after the trade is an alert only (no card, no order, the lot keeps its contract).
test("edit before trade: new card needing approval", async ({ page, driver }) => {
  const a1 = await driver.post("Bought GOOGL 357.5C 10/2 @ 0.71", [
    optionEntry("GOOGL", "357.5", "357.5C", "10/2", "0.71"),
  ])
  const first = await driver.cardFor("entry", { state: "pending" })
  const contract = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL").getByRole("heading", { level: 3 })
  await expect(contract).toHaveText("entry GOOGL 2026-10-02 357.5 call")

  await driver.advance(15)
  await driver.edit(a1, "Bought GOOGL 360C 10/2 @ 0.47", [optionEntry("GOOGL", "360", "360C", "10/2", "0.47")])

  const replaced = await driver.waitFor(
    () => driver.card(first.id),
    (c) => (c.state === "invalidated" ? c : null),
    "the first card invalidated",
  )
  expect(replaced.events.at(-1)).toMatchObject({ toState: "invalidated", reasonCode: "correction" })
  const second = await driver.cardFor("entry", { state: "pending" })
  expect(second.id).not.toBe(first.id)
  expect(second.providerPositionId).toBe(first.providerPositionId) // the same position, corrected
  expect(second.proposal).toMatchObject({ qty: 20, price: "0.49" })
  expect((await driver.card(second.id)).approval).toBeNull()
  expect(await driver.orders()).toEqual([]) // nothing traded on either version

  const card = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await expect(card).toHaveCount(1)
  await expect(contract).toHaveText("entry GOOGL 2026-10-02 360 call")
  await card.getByRole("button", { name: "Approve" }).click() // the re-approval
  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  expect(lot).toMatchObject({ qty: 20, contract: { strike: "360" } })
  expect((await driver.orders()).map((o) => o.cardId)).toEqual([second.id])
})

test("correction after trade: alert only", async ({ page, driver }) => {
  const a1 = await driver.post("Bought GOOGL 357.5C 10/2 @ 0.71", [
    optionEntry("GOOGL", "357.5", "357.5C", "10/2", "0.71"),
  ])
  await cardOnDesk(page, PROVIDER_A, "entry", "GOOGL").getByRole("button", { name: "Approve" }).click()
  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  const ordersBefore = await driver.orders()

  await driver.advance(25)
  await driver.post("correction: strike is 360", [
    {
      msg_type: "correction",
      fields: { strike: { value: "360", source: { kind: "literal", raw: "360" } }, correction_target_message_id: a1.id },
      link_hint: { target: null },
    },
  ])

  const alert = await driver.waitFor(
    () => driver.alerts(),
    (alerts) => alerts.find((a) => a.kind === "late_correction"),
    "the late-correction alert",
  )
  await expect(page.getByText(alert.message).first()).toBeVisible()
  expect(await driver.cards("pending")).toEqual([]) // no action proposed
  expect(await driver.orders()).toEqual(ordersBefore)
  const [unchanged] = (await driver.lots()).filter((l) => l.id === lot.id)
  expect(unchanged).toMatchObject({ qty: lot.qty, status: "open", contract: { strike: "357.5" } })
  expect(await driver.positionOf(PROVIDER_A, "GOOGL")).toMatchObject({ strike: "360", isCorrected: true })
})
