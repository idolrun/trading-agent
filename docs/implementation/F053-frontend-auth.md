# F053 — Passkey Login & Step-up UI

## Objective
Passkey-only sign-in, enrolment (with a console-issued token), logout, route protection and a reusable step-up dialog for manual entry and flatten.

## Requirements covered
- PRD §13, AC-10 (no password path; step-up for manual entry and flatten), C39, C42.

## Dependencies
- Depends on: F052 (backend F009)
- Produces: `/login`, `/enroll` routes, `useSession`, `<RequireSession>` gate, `useStepUp()` hook + `<StepUpDialog>`, `proxy.ts` optimistic redirect.
- Used by: F054–F061.

## Routes & components
| Route | Page | Server/client | Behaviour |
|---|---|---|---|
| `/login` | `app/login/page.tsx` | client component (WebAuthn needs `navigator.credentials`) | "Sign in with passkey" → `POST /auth/login/options` → `startAuthentication` → `POST /auth/login/verify` → redirect to `/` |
| `/enroll` | `app/enroll/page.tsx` | client | token from query string (`?token=`), label input, `startRegistration`, verify |
| all others | `app/(desk)/layout.tsx` | server layout + client gate | `GET /auth/session`; 401 → `/login` |
- `proxy.ts` (Next 16 Proxy): if no `session` cookie → redirect to `/login` (optimistic only; the backend remains the authority — per Next docs, Proxy is not full auth).
- `@simplewebauthn/browser` for options/response encoding (`pnpm add @simplewebauthn/browser`).
- `useStepUp()` returns `ensureStepUp(): Promise<void>` → options → `startAuthentication` → verify; mutations for manual entry/flatten call it first and retry once on `403 step_up_required`.
- No password field exists anywhere (lint rule: `grep -r 'type="password"'` in CI fails).

## States
Loading (spinner with label), error (message from `ApiError`, retry), unsupported browser (no WebAuthn → explanation), cancelled prompt (neutral message).

## Accessibility / responsive
Buttons labelled; focus moves to error; works on phone (iOS Safari PWA) and desktop.

## Files to create
`frontend/app/login/page.tsx`, `frontend/app/enroll/page.tsx`, `frontend/app/(desk)/layout.tsx` (move `app/page.tsx` + `app/cards/[cardId]` under the group), `frontend/proxy.ts`, `frontend/features/auth/{api.ts,use-session.ts,use-step-up.ts,step-up-dialog.tsx,login-screen.tsx,enroll-screen.tsx}`, tests `frontend/features/auth/*.test.tsx`.

## Files to modify
`frontend/lib/api/client.ts` (401 handler wiring), `frontend/app/layout.tsx` (providers).

## Tests
- Login success/cancel/error with mocked `@simplewebauthn/browser` + msw.
- Protected route without session → redirect.
- Step-up hook retries once after `403 step_up_required`.

## Acceptance criteria
AC-10 UI part: passkey-only; step-up dialog wired for protected actions.

## Implementation steps
1. Auth API module. 2. Login/enroll screens. 3. Session gate + route group. 4. Proxy redirect. 5. Step-up hook/dialog. 6. Tests.
