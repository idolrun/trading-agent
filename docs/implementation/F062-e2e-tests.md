# F062 — End-to-End Tests

## Objective
Browser-level tests of the full system (Next.js + FastAPI + PostgreSQL + fake Message API / broker / market data / models) for the flows the PRD calls out.

## Requirements covered
- PRD AC-18 (multi-provider isolation), AC-15, AC-16, AC-6, AC-4, AC-5, AC-14, AC-10 (UI), 7.1–7.9 happy paths, §17 ("every order path exercised" — on fakes here; on paper in F064).

## Dependencies
- Depends on: F055–F061
- Produces: Playwright suite + an `e2e` compose/profile that starts backend (fakes enabled), frontend, PG.
- Used by: F064 (CI gate).

## Scope
- `pnpm add -D @playwright/test`; `frontend/e2e/`. Backend started with `Settings.environment="test"`, fake broker/market data, `FixtureInterpreter` or recorded fake model responses, a test-only enrolment path using a virtual WebAuthn authenticator (Playwright/CDP `WebAuthn.addVirtualAuthenticator`).
- Test driver posts webhook events to `/ingest` with HMAC (test secret) and advances a controllable clock endpoint that exists **only** in `environment=test` (never in production — asserted).
| Scenario | Expected |
|---|---|
| AC-18 A1, B2, A3, B4 | A3 card on A's GOOGL lot, B4 on B's TSLA lot |
| Entry → approve → fill → trim half on 1-lot | blank qty + "lot too small" label; approve blocked until qty |
| Entry 2 contracts with 3 TPs | TP1 1, TP2 1 displayed and placed |
| Entry card timeout / price band / superseded | card expired/invalidated; no order |
| Edit before trade / correction after trade | new card needing approval / alert only |
| Missing underlying "1000c" | review card; approve disabled until confirmation |
| 0DTE lot unanswered | cutoff-1 card, then forced close at cutoff 2 |
| Manual entry | step-up prompt then order |
| Feed silence | degraded banner |

## Files to create
`frontend/playwright.config.ts`, `frontend/e2e/*.spec.ts`, `frontend/e2e/support/{driver.ts,webauthn.ts}`, `compose.e2e.yaml` (or documented commands), backend test-only clock route guarded by environment.

## Acceptance criteria
Suite green locally and in CI; each scenario asserts DB side effects via API (orders, cards) not just UI text.

## Implementation steps
1. Harness (services, virtual authenticator, driver). 2. Scenarios one by one. 3. CI wiring.
