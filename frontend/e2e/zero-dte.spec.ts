import { approveWith, cardOnDesk, CUTOFF_1, CUTOFF_2, expect, lit, optionEntry, PROVIDER_A, test } from "./support/driver"

// AC-6 / 7.6: a lot expiring today gets an urgent close card at cutoff 1 (close - 30 min);
// unanswered, cutoff 2 (close - 10 min) cancels its protective orders and force-closes it.
// Starts at 15:00 ET so each clock jump stays inside the 60 min session idle limit.
test.use({ start: new Date("2026-10-02T19:00:05Z") })

test("0DTE lot unanswered: cutoff-1 card, then forced close at cutoff 2", async ({ page, driver }) => {
  await driver.post("Bought GOOGL 352.5C 0dte @ 3.30 SL 2.50 TP 4.00 5.00", [
    optionEntry("GOOGL", "352.5", "352.5C", "0dte", "3.30", {
      stop_level: lit("2.50"),
      tp_levels: ["4.00", "5.00"].map((v) => lit(v)),
    }),
  ])
  await approveWith(cardOnDesk(page, PROVIDER_A, "entry", "GOOGL"), 2)
  const lot = await driver.lotOf(PROVIDER_A, "GOOGL")
  const protective = await driver.waitFor(
    () => driver.orders(),
    (orders) => {
      const legs = orders.filter((o) => o.lotId === lot.id && (o.kind === "stop" || o.kind === "tp"))
      return legs.length === 3 && legs.every((o) => o.status === "working") ? legs : undefined
    },
    "the bracket (stop + 2 TPs) working",
  )

  await driver.clockTo(CUTOFF_1)
  const close = await driver.cardFor("close", { state: "pending" })
  expect(close).toMatchObject({ lotId: lot.id })
  expect(close.proposal).toMatchObject({ qty: lot.qty, labels: ["urgent"] })
  const onDesk = page.getByRole("article").filter({ hasText: "Urgent" })
  await expect(onDesk).toHaveCount(1)
  await expect(onDesk.getByRole("button", { name: /Close entire position/ })).toBeEnabled()
  expect((await driver.orders()).filter((o) => o.kind === "exit")).toEqual([])

  await driver.clockTo(CUTOFF_2) // nobody answered
  await driver.waitFor(() => driver.card(close.id), (c) => c.state === "invalidated", "the close card invalidated")
  await expect(onDesk).toHaveCount(0)
  const forced = await driver.waitFor(
    () => driver.orders(),
    (orders) => orders.find((o) => o.kind === "exit" && o.autoReason === "eod_forced_close"),
    "the forced close order",
  )
  expect(forced).toMatchObject({ side: "sell", qty: lot.qty, lotId: lot.id, cardId: null }) // no approval
  const orders = await driver.orders()
  // Re-prices of the forced close are its children; nothing else sells the lot.
  expect(orders.filter((o) => o.kind === "exit").every((o) => o.cardId === null && o.lotId === lot.id)).toBe(true)
  for (const leg of protective) {
    expect(orders.find((o) => o.id === leg.id)?.status, `${leg.kind} ${leg.id}`).toBe("cancelled")
  }
  const alert = await driver.waitFor(
    () => driver.alerts(),
    (alerts) => alerts.find((a) => a.kind === "eod_forced_close"),
    "the forced-close alert",
  )
  await expect(page.getByText(alert.message).first()).toBeVisible()
  await driver.waitFor(
    () => driver.lots(),
    (lots) => lots.find((l) => l.id === lot.id && l.status === "closed"),
    "the lot closed",
  )
})
