# F052 — Frontend Foundation & Contract

## Objective
Point the existing Next.js 16 app at the real FastAPI backend through a same-origin proxy, generate TypeScript types from the backend's OpenAPI contract, delete the mock API and duplicated business rules, and add a frontend test harness — before building any new screen.

## Requirements covered
- Decision log: C17.
- PRD FR-25 (responsive web dashboard), §13 (public dashboard, session cookies), X1 (Next.js instead of TRD's HTMX — README §10.2), X4 (no duplicated business rules).
- Request §13–§14 (App Router; UI → API client → backend; do not duplicate backend rules).

## Dependencies
- Depends on: **Backend Completion Checklist** (README §7) — in particular F030 `openapi.json`.
- Produces: `frontend/lib/api/schema.d.ts` (generated), typed `apiGet/apiPost` wrappers, `next.config.ts` rewrites, `pnpm gen:api`, Vitest + Testing Library setup.
- Used by: F053–F061.

## Scope
- **Read first:** `frontend/node_modules/next/dist/docs/` (per `frontend/AGENTS.md`) — notably `rewrites`, `proxy.ts` (Next 16 renamed Middleware to Proxy), App Router data fetching.
- **Same-origin proxy:** `next.config.ts` `rewrites()` → `{ source: "/api/:path*", destination: `${process.env.BACKEND_ORIGIN}/api/:path*` }` (server-only env, not `NEXT_PUBLIC_*`). Browser always calls `/api/v1/...` on the dashboard origin → session cookie `SameSite=Strict` and the dashboard-origin check work. Remove `NEXT_PUBLIC_API_BASE_URL` from `lib/api/client.ts`.
- **Contract types:** `pnpm add -D openapi-typescript`; script `gen:api` = `openapi-typescript ./openapi.json -o lib/api/schema.d.ts`; feature `types.ts` files re-export/alias generated types (`components["schemas"]["Card"]`) instead of hand-written shapes; a CI check (`pnpm gen:api && git diff --exit-code`) catches drift.
- **Delete mocks:** `frontend/app/api/v1/**` (route handlers + `_store.ts`, `_http.ts`), `frontend/features/cards/decision.ts` (`applyCardDecision` duplicates backend rules). `useDecideCard` optimistic update becomes "mark card as deciding" only; the server response is the truth.
- **Discord IDs are strings** in the generated types — no numeric parsing anywhere.
- **Tests:** `pnpm add -D vitest @testing-library/react @testing-library/user-event jsdom @vitejs/plugin-react`; `vitest.config.ts`; script `test`. Mock HTTP at the client boundary with `msw` (`pnpm add -D msw`) using fixtures shaped by generated types.
- **Errors:** keep `ApiError` mapping of `{message, code}`; add `401 → redirect to /login` handler (used by F053) and `403 step_up_required` code handling hook.

## Out of scope
Auth screens (F053), SSE (F054), new views (F055+).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `frontend/lib/api/schema.d.ts` | generated types (committed) | hand edits |
| `frontend/openapi.json` | copied from backend export (F030) | — |
| `frontend/vitest.config.ts`, `frontend/test/setup.ts`, `frontend/test/msw.ts` | test harness | — |
| `frontend/.env.example` | `BACKEND_ORIGIN=http://127.0.0.1:8000` | secrets |

## Files to modify
- `frontend/next.config.ts` (rewrites), `frontend/package.json` (scripts, dev deps), `frontend/lib/api/client.ts` (no base URL, 401 handling), `frontend/features/*/types.ts` (alias generated types; keep enum arrays only where UI needs lists), `frontend/features/cards/mutations.ts` (remove `applyCardDecision`), `frontend/features/cards/queries.ts` (list shape `{items, nextCursor}`).
- Delete: `frontend/app/api/v1/**`, `frontend/features/cards/decision.ts`.

## Tests
- Unit: client maps error bodies; 401 triggers redirect handler.
- Type check: `pnpm tsc --noEmit` passes against generated types.
- Existing screens render with msw fixtures (smoke).

## Acceptance criteria
- `pnpm dev` with backend running shows real data (empty state OK) through `/api/v1`; no mock route remains; `pnpm lint`, `pnpm tsc --noEmit`, `pnpm test` pass; `gen:api` produces no diff.

## Implementation steps
1. Read bundled Next docs (rewrites, proxy). 2. Rewrites + env. 3. OpenAPI types + script. 4. Refactor feature types to generated ones. 5. Delete mocks and `decision.ts`; simplify optimistic mutation. 6. Test harness. 7. Smoke tests.
