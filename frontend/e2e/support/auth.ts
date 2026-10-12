import { execFileSync } from "node:child_process"
import { expect, type APIRequestContext, type Page } from "@playwright/test"
import { BACKEND_DIR, backendEnv, ORIGIN } from "./env"

/** The desk user of the E2E database (test values only; created by global-setup.ts). */
export const E2E_EMAIL = "e2e-desk@example.com"
export const E2E_PASSWORD = "e2e-desk-password-2026"

/** Console provisioning, as an operator would (`app.auth.cli create-user`): the temporary password. */
export function createUser(email: string): string {
  const out = execFileSync("uv", ["run", "python", "-m", "app.auth.cli", "create-user", email], {
    cwd: BACKEND_DIR,
    env: { ...process.env, ...backendEnv },
    encoding: "utf8",
  })
  const temp = out.trim().split("\n").at(-1)?.trim()
  if (!temp) throw new Error(`create-user printed no password: ${out}`)
  return temp
}

/** API-level first sign-in: replace the temporary password (the UI flow is covered in auth.spec). */
export async function activate(api: APIRequestContext, email: string, temp: string, password: string) {
  const login = await api.post(`${ORIGIN}/api/v1/auth/login`, { data: { email, password: temp } })
  expect(login.status(), await login.text()).toBe(200)
  const change = await api.post(`${ORIGIN}/api/v1/auth/change-password`, {
    data: { currentPassword: temp, newPassword: password },
  })
  expect(change.status(), await change.text()).toBe(204)
}

/** Sign in through the login form and land on the desk. */
export async function signIn(page: Page, email = E2E_EMAIL, password = E2E_PASSWORD) {
  await page.goto("/")
  await expect(page).toHaveURL(/\/login/)
  await fillSignIn(page, email, password)
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible()
}

export async function fillSignIn(page: Page, email: string, password: string) {
  await page.getByLabel("Email").fill(email)
  await page.getByLabel("Password", { exact: true }).fill(password)
  await page.getByRole("button", { name: "Sign in" }).click()
}
