import { approveWith, cardOnDesk, expect, optionEntry, lit, PROVIDER_A, test } from "./support/driver"

// AC-16: an entry the user sizes at 2 contracts with 3 take-profits shows and places TP1 1 + TP2 1
// (TP3 dropped: never more legs than contracts), with the stop for the whole lot, after the fill.
test("entry of 2 contracts with 3 TPs", async ({ page, driver }) => {
  const text = "Bought GOOGL 352.5C 10/2 @ 3.30 SL 2.50 TP 4.00 5.00 6.00"
  await driver.post(text, [
    optionEntry("GOOGL", "352.5", "352.5C", "10/2", "3.30", {
      stop_level: lit("2.50"),
      tp_levels: ["4.00", "5.00", "6.00"].map((v) => lit(v)),
    }),
  ])

  const entry = await driver.cardFor("entry", { state: "pending" })
  expect(entry.proposal.qty).toBeNull()
  expect(entry.proposal.tpLegs).toEqual([]) // the split exists once the user's quantity does
  expect(entry.proposal.tpLevels).toEqual(["4", "5", "6"])

  const card = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await expect(card.getByLabel("Quantity", { exact: true })).toHaveValue("")
  await expect(card.getByLabel("TP1 quantity")).toHaveCount(0)
  await card.getByLabel("Quantity", { exact: true }).fill("2")
  await expect(card.getByText("Take-profit split (adds up to 2)")).toBeVisible()
  await expect(card.getByLabel("TP1 quantity")).toHaveValue("1")
  await expect(card.getByLabel("TP2 quantity")).toHaveValue("1")
  await expect(card.getByLabel("TP3 quantity")).toHaveCount(0)
  await expect(card.getByLabel("Targets and stop")).toContainText("$2.50")
  await card.getByRole("button", { name: "Approve" }).click()
  await expect(card).toBeHidden()
  expect((await driver.card(entry.id)).approval).toMatchObject({ edits: { qty: 2 } })

  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  expect(lot.qty).toBe(2)
  const bracket = await driver.waitFor(
    () => driver.orders(),
    (orders) => {
      const working = orders.filter((o) => o.lotId === lot.id && o.status === "working")
      return working.length === 3 ? working : undefined
    },
    "the stop and two TP orders",
  )
  const tps = bracket.filter((o) => o.kind === "tp").sort((a, b) => Number(a.limitPrice) - Number(b.limitPrice))
  expect(tps.map((o) => [o.qty, o.limitPrice])).toEqual([
    [1, "4"],
    [1, "5"],
  ])
  const [stop] = bracket.filter((o) => o.kind === "stop")
  expect(stop).toMatchObject({ qty: 2, stopPrice: "2.5", side: "sell" })
})
