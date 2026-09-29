import { expect, test } from "./support/driver"

// FR-4 / AC-8: no message or heartbeat for more than feed_silence_s (600 s) during regular hours
// raises feed_degraded and the desk shows the degraded banner; a heartbeat clears it.
test("feed silence: degraded banner", async ({ page, driver }) => {
  const banner = page.getByRole("alert").filter({ hasText: "Feed degraded. Messages may be missing or late." })
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible()
  await expect(banner).toHaveCount(0)
  expect((await driver.flags()).feed_degraded).toBe(false)

  await driver.advance(601, { heartbeat: false }) // 10:40 ET, not a word from the Message API
  await driver.waitFor(() => driver.flags(), (flags) => flags.feed_degraded, "feed_degraded set")
  await expect(banner).toBeVisible()
  const alert = await driver.waitFor(
    () => driver.alerts(),
    (alerts) => alerts.find((a) => a.kind === "system_flag" && a.message.startsWith("feed_degraded activated")),
    "the feed_degraded alert",
  )
  expect(alert.severity).toBe("urgent")

  await driver.heartbeat() // the feed is back
  await driver.waitFor(() => driver.flags(), (flags) => !flags.feed_degraded, "feed_degraded cleared")
  await expect(banner).toBeHidden()
})
