# F053 — Password Login, User Management & Step-up UI

> **Revised 2026-09-29** with F009: passkeys were replaced by email + password.

## Objective
Email + password sign-in, forced change of temporary passwords, logout, route protection, a reusable password step-up dialog for protected actions, and screens to manage users and read the audit log.

## Requirements covered
- PRD §13 (step-up for manual entry and flatten), C39, C42. Original AC-10 (passkey only) superseded.

## Dependencies
- Depends on: F052 (backend F009)
- Produces: `/login`, `/change-password`, `/users`, `/audit`, `useSession`, `<RequireSession>` gate, `useStepUp()` + `<StepUpDialog>`, `proxy.ts` optimistic redirect.
- Used by: F054–F061.

## Routes & components
| Route | Page | Behaviour |
|---|---|---|
| `/login` | `app/login/page.tsx` | email + password form → `POST /auth/login`; `mustChangePassword` → `/change-password`, else `next` |
| `/change-password` | `app/change-password/page.tsx` | current + new + repeat (≥12 chars) → `POST /auth/change-password` → `/` |
| `/users` (desk) | `features/users/user-manager.tsx` | list; add by email (temporary password shown once in a dialog with Copy); deactivate/reactivate (not yourself); reset password |
| `/audit` (desk) | `features/users/audit-log.tsx` | newest first, filter by action, load more |
| all desk routes | `app/(desk)/layout.tsx` | `GET /auth/session`; 401 → `/login`; a pending temporary password → `/change-password`; header shows the email and Sign out |
- `proxy.ts`: no `session` cookie → redirect to `/login` (optimistic only; the backend is the authority).
- `useStepUp()`: `withStepUp(fn)` retries once after the user confirms their password in `<StepUpDialog>` (`POST /auth/step-up`) on `403 step_up_required`. The typed password is discarded when the dialog closes.

## States
Loading spinner with label, error alert (focused; message from `ApiError`), Try again after a failure, repeat-password mismatch.

## Files
`frontend/features/auth/{api,submit,use-session,use-step-up}.ts(x)`, `login-screen.tsx`, `change-password-screen.tsx`, `step-up-dialog.tsx`, `require-session.tsx`; `frontend/features/users/{api.ts,user-manager.tsx,audit-log.tsx}`; tests `features/auth/*.test.tsx`, `features/users/users.test.tsx`. `@simplewebauthn/browser`, the enrol page and the `lint:no-password` script are gone.

## Acceptance criteria
Sign-in, forced password change, logout, gate and step-up work against the F009 API; users can be added, deactivated, reactivated and reset from the UI; audit log is readable.
