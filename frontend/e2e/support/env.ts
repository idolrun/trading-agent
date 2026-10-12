import { mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

/**
 * Where the E2E stack runs. Ports differ from dev (3000/8000) so a running `next dev` or dev
 * backend is never reused or clobbered. The session cookie is bound to the User-Agent, so the
 * browser and every API request context use UA.
 */
export const ROOT = path.resolve(__dirname, "../../..")
export const BACKEND_DIR = path.join(ROOT, "backend")
export const WEB_PORT = 3100
export const API_PORT = 8100
export const ORIGIN = `http://localhost:${WEB_PORT}` // not 127.0.0.1: the dashboard origin
export const BACKEND = `http://127.0.0.1:${API_PORT}`
export const UA = "trading-agent-e2e/1.0 (Playwright Chromium)"
export const HMAC_SECRET = "e2e-ingest-secret-not-for-production"
export const E2E_DB = "trading_e2e"

/** Repo-root `.env` (local dev); real environment variables win (CI). */
function dotenv(): Record<string, string> {
  try {
    const text = readFileSync(path.join(ROOT, ".env"), "utf8")
    return Object.fromEntries(
      text
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith("#") && line.includes("="))
        .map((line) => {
          const at = line.indexOf("=")
          return [line.slice(0, at).trim(), line.slice(at + 1).trim().replace(/^(['"])(.*)\1$/, "$2")]
        }),
    )
  } catch {
    return {}
  }
}

const fileEnv = dotenv()
const env = (name: string, fallback?: string) => process.env[name] ?? fileEnv[name] ?? fallback

/** The same server/roles as DATABASE_URL, database `trading_e2e` (never the shared trading_test). */
function onE2eDb(url: string | undefined, name: string): string {
  if (!url) throw new Error(`${name} is not set (repo-root .env or the environment)`)
  return url.replace(/\/[^/?]+(\?|$)/, `/${E2E_DB}$1`)
}

/** Recorded interpretations (FixtureInterpreter) for this run: one fresh directory per process tree. */
const fixturesDir =
  process.env.E2E_INTERPRETATIONS_DIR ?? mkdtempSync(path.join(tmpdir(), "trading-e2e-interpretations-"))
process.env.E2E_INTERPRETATIONS_DIR = fixturesDir

/** Environment of the E2E backend and of the console CLI run against its database. */
export const backendEnv: Record<string, string> = {
  ENVIRONMENT: "test",
  BROKER_ADAPTER: "fake", // the suite drives the scriptable fakes, whatever the developer's .env selects
  MARKETDATA_ADAPTER: "fake",
  LOG_LEVEL: "WARNING",
  DATABASE_URL: onE2eDb(env("DATABASE_URL"), "DATABASE_URL"),
  DATABASE_OWNER_URL: onE2eDb(env("DATABASE_OWNER_URL"), "DATABASE_OWNER_URL"),
  DASHBOARD_ORIGIN: ORIGIN,
  INGEST_HMAC_SECRET: HMAC_SECRET,
  INTERPRETATION_FIXTURES_DIR: fixturesDir,
  AUTH_RATE_LIMIT_PER_MIN: "10000", // every test signs in through the form
  // Superuser, only to create the database once (backend.sh).
  POSTGRES_USER: env("POSTGRES_USER", "postgres")!,
  POSTGRES_PASSWORD: env("POSTGRES_PASSWORD", "")!,
  POSTGRES_HOST: env("POSTGRES_HOST", "127.0.0.1")!,
  POSTGRES_PORT: env("POSTGRES_PORT", "5432")!,
  E2E_DB,
  API_PORT: String(API_PORT),
}
