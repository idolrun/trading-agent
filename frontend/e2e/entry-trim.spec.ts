import { cardOnDesk, expect, followUp, optionEntry, PROVIDER_A, test } from "./support/driver"

// PRD 7.1 + 7.3 / AC-15: message -> card -> approve on the desk -> shadow fill -> lot; then a
// "take half" on a 1-lot gets a blank-qty trim card ("lot too small") until the user types one.
test("entry -> approve -> fill -> trim half on a 1-lot", async ({ page, driver }) => {
  await driver.post("Bought GOOGL 357.5C 10/2 @ 0.71", [optionEntry("GOOGL", "357.5", "357.5C", "10/2", "0.71")])

  const entryCard = cardOnDesk(page, PROVIDER_A, "entry", "GOOGL")
  await expect(entryCard).toBeVisible()
  const qty = entryCard.getByLabel("Quantity", { exact: true })
  await expect(qty).toHaveValue("13") // $1000 tier / (0.74 * 100)
  await qty.fill("1")
  await entryCard.getByRole("button", { name: "Approve" }).click()
  await expect(entryCard).toBeHidden()

  const entry = await driver.cardFor("entry", { providerId: PROVIDER_A })
  const detail = await driver.card(entry.id)
  expect(detail.approval).toMatchObject({ decision: "approve", edits: { qty: 1 } })
  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  expect(lot).toMatchObject({ qty: 1, providerPositionId: entry.providerPositionId })
  const [buy] = (await driver.orders()).filter((o) => o.cardId === entry.id)
  expect(buy).toMatchObject({ kind: "entry", side: "buy", qty: 1, filledQty: 1, limitPrice: "0.74", status: "filled", mode: "shadow" })

  await driver.advance(20)
  await driver.post("Take half off @ 2.10", [followUp("trim", entry.providerPositionId, { exit_fraction: "half" })])
  const trim = await driver.cardFor("trim", { state: "pending" })
  expect(trim.lotId).toBe(lot.id)
  expect(trim.proposal.qty).toBeNull()
  expect(trim.proposal.labels).toContain("lot_too_small")

  const trimCard = cardOnDesk(page, PROVIDER_A, "trim", "GOOGL")
  await expect(trimCard.getByRole("listitem").filter({ hasText: "Lot too small" })).toBeVisible()
  await expect(trimCard.getByText("Lot too small to trim: exit all or skip.")).toBeVisible()
  await expect(trimCard.getByLabel("Quantity", { exact: true })).toHaveValue("")
  const approve = trimCard.getByRole("button", { name: "Approve" })
  await expect(approve).toBeDisabled()
  expect((await driver.orders()).filter((o) => o.cardId === trim.id)).toEqual([])

  await trimCard.getByLabel("Quantity", { exact: true }).fill("1")
  await expect(approve).toBeEnabled()
  await approve.click()
  await expect(trimCard).toBeHidden()

  expect((await driver.card(trim.id)).approval).toMatchObject({ decision: "approve", edits: { qty: 1 } })
  const sell = await driver.waitFor(
    () => driver.orders(),
    (orders) => orders.find((o) => o.cardId === trim.id && o.kind === "exit"),
    "the trim's exit order",
  )
  expect(sell).toMatchObject({ side: "sell", qty: 1, lotId: lot.id })
})
