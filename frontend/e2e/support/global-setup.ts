import { request } from "@playwright/test"
import { activate, createUser, E2E_EMAIL, E2E_PASSWORD } from "./auth"
import { BACKEND, ORIGIN, UA } from "./env"

/**
 * Runs after the web servers are up (the backend's start script created and migrated
 * trading_e2e). Empties the database, users included, and provisions the desk user with a
 * known password; every test then signs in through the login form.
 */
export default async function globalSetup() {
  const api = await request.newContext({ userAgent: UA, extraHTTPHeaders: { Origin: ORIGIN } })
  try {
    const reset = await api.post(`${BACKEND}/api/v1/test/reset`, { data: { keepUsers: false } })
    if (!reset.ok()) throw new Error(`reset failed: ${reset.status()} ${await reset.text()}`)
    await activate(api, E2E_EMAIL, createUser(E2E_EMAIL), E2E_PASSWORD)
  } finally {
    await api.dispose()
  }
}
