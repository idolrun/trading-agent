import { test as base, expect } from "@playwright/test"
import { createUser, fillSignIn } from "./support/auth"

// F009 / F053: a console-provisioned user signs in through the form with the temporary
// password, is forced to replace it, then reaches the desk; a wrong password is refused.
base("first sign-in: temporary password, forced change, desk", async ({ page }) => {
  const email = `e2e-new-${Date.now()}@example.com`
  const temp = createUser(email) // app.auth.cli create-user, as an operator would

  await page.goto("/positions")
  await expect(page).toHaveURL(/\/login\?next=%2Fpositions/)
  await fillSignIn(page, email, "not-the-password")
  await expect(page.getByRole("alert")).toBeVisible()
  await expect(page).toHaveURL(/\/login/)

  await fillSignIn(page, email, temp)
  await expect(page).toHaveURL(/\/change-password/)
  await expect(page.getByText("Replace your temporary password to continue.")).toBeVisible()
  const blocked = await page.request.get("/api/v1/cards")
  expect(blocked.status()).toBe(403)
  expect((await blocked.json()).code).toBe("password_change_required")

  await page.getByLabel("Current password").fill(temp)
  await page.getByLabel("New password (at least 12 characters)").fill("a-brand-new-password")
  await page.getByLabel("Repeat new password").fill("a-brand-new-password")
  await page.getByRole("button", { name: "Change password" }).click()
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible()
  expect((await page.request.get("/api/v1/cards")).status()).toBe(200)
})
