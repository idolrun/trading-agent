import { approveWith, cardOnDesk, expect, followUp, lit, optionEntry, PROVIDER_A, PROVIDER_B, test } from "./support/driver"

// AC-18 multi-provider isolation: A1, B2, A3, B4 in one channel. Each follow-up lands on its own
// author's position and lot: A3 (trim) on A's GOOGL lot, B4 (stop) on B's TSLA lot.
test("AC-18: A1, B2, A3, B4 stay with their own provider", async ({ page, driver }) => {
  // A1 (T0) and B2 (T0 + 10 s): both entries approved on the desk, so both providers hold lots.
  await driver.post("Bought GOOGL 357.5C 10/2 @ 0.71", [optionEntry("GOOGL", "357.5", "357.5C", "10/2", "0.71")])
  await approveWith(cardOnDesk(page, PROVIDER_A, "entry", "GOOGL"), 2)
  const lotA = await driver.lotOf(PROVIDER_A, "GOOGL")

  await driver.advance(5)
  await driver.post("Bought TSLA 400C 10/2 @ 3.10", [optionEntry("TSLA", "400", "400C", "10/2", "3.10")], {
    author: PROVIDER_B,
  })
  await approveWith(cardOnDesk(page, PROVIDER_B, "entry", "TSLA"), 1)
  const lotB = await driver.lotOf(PROVIDER_B, "TSLA")
  const positionA = await driver.positionOf(PROVIDER_A, "GOOGL")
  const positionB = await driver.positionOf(PROVIDER_B, "TSLA")
  expect([lotA.providerPositionId, lotB.providerPositionId]).toEqual([positionA.id, positionB.id])

  // A3 (T0 + 20 s): "take half" from A; B4 (T0 + 30 s): "stop 1.50" from B.
  await driver.advance(10)
  await driver.post("Take half off @ 2.10", [followUp("trim", positionA.id, { exit_fraction: "half" })])
  await driver.advance(10)
  await driver.post("Stop 1.50", [followUp("stop_move", positionB.id, { stop_level: lit("1.50") })], {
    author: PROVIDER_B,
  })

  const a3 = await driver.cardFor("trim", { state: "pending" })
  expect(a3).toMatchObject({ providerId: PROVIDER_A, providerPositionId: positionA.id, lotId: lotA.id })
  expect(a3.proposal.qty).toBeNull() // "half" is the provider's word; the user types the quantity
  const b4 = await driver.waitFor(
    () => driver.cards("pending"),
    (cards) => cards.find((c) => c.providerId === PROVIDER_B && c.action !== "entry"),
    "B4's card",
  )
  expect(b4).toMatchObject({ action: "stop", providerPositionId: positionB.id, lotId: lotB.id })

  // The provider ledgers moved independently.
  expect(await driver.positionOf(PROVIDER_A, "GOOGL")).toMatchObject({ remainingFraction: "0.5", stopLevel: null })
  expect(await driver.positionOf(PROVIDER_B, "TSLA")).toMatchObject({ remainingFraction: "1", stopLevel: "1.5" })

  // On the desk: each card under its own author and contract.
  await expect(cardOnDesk(page, PROVIDER_A, "trim", "GOOGL")).toBeVisible()
  await expect(cardOnDesk(page, PROVIDER_B, b4.action, "TSLA")).toBeVisible()
  await expect(cardOnDesk(page, PROVIDER_B, "trim")).toHaveCount(0)
  await expect(cardOnDesk(page, PROVIDER_A, b4.action)).toHaveCount(0)
})
