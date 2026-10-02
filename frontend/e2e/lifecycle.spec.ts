import { cardOnDesk, expect, followUp, lit, optionEntry, PROVIDER_A, test } from "./support/driver"

// Shadow lifecycle (PRD 7.1-7.4): signal -> card -> approve -> simulated fill -> lot + bracket ->
// "take half off" -> trim card -> approve -> partial exit -> "exit" -> approve -> lot and
// position closed, no working order left. Shadow mode only: nothing reaches a broker.
test("entry -> bracket -> trim -> exit -> closed", async ({ page, driver }) => {
  await driver.post("Bought GOOGL 352.5C 10/2 @ 3.30 SL 2.50 TP 4.00 5.00", [
    optionEntry("GOOGL", "352.5", "352.5C", "10/2", "3.30", {
      stop_level: lit("2.50"),
      tp_levels: ["4.00", "5.00"].map((v) => lit(v)),
    }),
  ])

  const entryCard = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await expect(entryCard).toBeVisible()
  await expect(entryCard.getByLabel("Quantity", { exact: true })).toHaveValue("2")
  await entryCard.getByRole("button", { name: "Approve" }).click()
  await expect(entryCard).toBeHidden()

  // fill -> one lot linked to the provider position; stop for the lot, TPs never above it
  const entry = await driver.cardFor("entry", { providerId: PROVIDER_A })
  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  expect(lot).toMatchObject({ qty: 2, providerPositionId: entry.providerPositionId })
  const protection = await driver.waitFor(
    () => driver.orders(),
    (orders) => {
      const legs = orders.filter((o) => o.lotId === lot.id && o.status === "working")
      return legs.length === 3 ? legs : undefined
    },
    "the stop and two TP orders",
  )
  expect(protection.find((o) => o.kind === "stop")).toMatchObject({ qty: 2, stopPrice: "2.5" })
  expect(protection.filter((o) => o.kind === "tp").reduce((n, o) => n + o.qty, 0)).toBeLessThanOrEqual(2)
  expect((await driver.orders()).filter((o) => o.cardId === entry.id && o.kind === "entry")).toHaveLength(1)

  // "Take half off" -> a trim card for 1 of the 2, approved by the user
  await driver.advance(20)
  await driver.post("Take half off", [followUp("trim", entry.providerPositionId, { exit_fraction: "half" })])
  const trim = await driver.cardFor("trim", { state: "pending" })
  expect(trim).toMatchObject({ lotId: lot.id, providerPositionId: entry.providerPositionId })
  expect(trim.proposal.qty).toBe(1)
  expect((await driver.orders()).filter((o) => o.cardId === trim.id)).toEqual([]) // approval first
  const trimCard = cardOnDesk(page, PROVIDER_A, "trim", "GOOGL")
  await expect(trimCard.getByLabel("Quantity", { exact: true })).toHaveValue("1")
  await trimCard.getByRole("button", { name: "Approve" }).click()
  await expect(trimCard).toBeHidden()

  await expect
    .poll(async () => (await driver.lots()).find((l) => l.id === lot.id)?.qty, { message: "the lot after the trim" })
    .toBe(1)
  const afterTrim = (await driver.lots()).find((l) => l.id === lot.id)
  expect(afterTrim).toMatchObject({ status: "open", providerPositionId: entry.providerPositionId })
  const stops = await driver.waitFor(
    () => driver.orders(),
    (orders) => {
      const stop = orders.filter((o) => o.lotId === lot.id && o.kind === "stop" && o.status === "working")
      return stop.length === 1 && stop[0].qty === 1 ? stop : undefined
    },
    "the stop resized to the remaining contract",
  )
  expect(stops).toHaveLength(1)

  // "Exit" -> sells the rest: lot and provider position close, nothing is left working
  await driver.advance(20)
  await driver.post("All out", [followUp("full_exit", entry.providerPositionId)])
  const exit = await driver.cardFor("exit", { state: "pending" })
  expect(exit.proposal.qty).toBe(1)
  const exitCard = cardOnDesk(page, PROVIDER_A, "exit", "GOOGL")
  await exitCard.getByRole("button", { name: "Approve" }).click()
  await expect(exitCard).toBeHidden()

  await expect
    .poll(async () => (await driver.lots()).find((l) => l.id === lot.id)?.status, { message: "the lot closes" })
    .toBe("closed")
  expect((await driver.lots()).find((l) => l.id === lot.id)?.qty).toBe(0)
  await expect
    .poll(async () => (await driver.positions(PROVIDER_A)).find((p) => p.id === entry.providerPositionId)?.status, {
      message: "the provider position closes",
    })
    .toBe("closed")
  const orders = await driver.orders()
  expect(orders.filter((o) => o.status === "working")).toEqual([]) // no orphaned stop or TP
  expect(orders.every((o) => o.mode === "shadow")).toBe(true)
})
