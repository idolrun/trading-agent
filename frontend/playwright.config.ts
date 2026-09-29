import { defineConfig, devices } from "@playwright/test"
import { API_PORT, BACKEND, backendEnv, ORIGIN, UA, WEB_PORT } from "./e2e/support/env"

/**
 * Browser E2E (F062): FastAPI (ENVIRONMENT=test, fakes) + a production Next build + PostgreSQL
 * database trading_e2e. `pnpm e2e`; see e2e/README.md.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // one backend, one clock, one database
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./e2e/support/global-setup.ts",
  use: {
    baseURL: ORIGIN,
    userAgent: UA,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], userAgent: UA } }],
  webServer: [
    {
      command: "sh e2e/support/backend.sh",
      url: `${BACKEND}/health`,
      env: backendEnv,
      timeout: 120_000,
      reuseExistingServer: false, // must be the ENVIRONMENT=test backend on trading_e2e
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // A separate build dir: never clobbers `.next` (dev / prod builds) with the E2E rewrite.
      command: `next build && next start --port ${WEB_PORT}`,
      url: `${ORIGIN}/login`,
      env: { NEXT_DIST_DIR: ".next-e2e", BACKEND_ORIGIN: `http://127.0.0.1:${API_PORT}` },
      timeout: 300_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
