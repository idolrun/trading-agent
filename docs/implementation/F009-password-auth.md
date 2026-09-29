# F009 — Password Auth, Users & Sessions (backend)

> **Revised 2026-09-29.** The original F009 was passkey-only (WebAuthn). It was replaced by email + password against an allowlist `users` table. PRD §13 / AC-10 ("passkey only, no password") are superseded by this document.

## Objective
Email + password authentication for an allowlisted set of users: console provisioning, login, short server-side sessions, password step-up, forced change of temporary passwords, user management, rate limiting and an append-only audit log — plus the `require_session` / `require_step_up` dependencies every protected route uses.

## Requirements covered
- PRD §13 (public dashboard, short device-bound sessions, rate limiting, step-up for manual entry and flatten), C39, C42. Passkey-only / single user / no password (original AC-10) intentionally replaced.
- TRD §10 (server-side sessions, `SameSite=Strict` cookies).

## Dependencies
- Depends on: F004
- Produces: `/api/v1/auth/*`, `/api/v1/users*`, `/api/v1/audit`, `require_session`, `require_step_up`, `AuthContext(session_id, user_id, email, …)`, `AuditService`, CLI `app.auth.cli`.
- Used by: every `/api/v1` route; F036 (approvals record `session_id`), F047 (step-up), F053 (UI).

## Scope
- **Allowlist, no roles, no self-registration.** `users(user_id, email unique lowercased, password_hash, is_active, must_change_password, created_at, updated_at, last_login_at)`. There is **no public register endpoint**; an unknown or inactive email cannot sign in and never creates an account. Every active user has the same access.
- **Bootstrap / recovery (console):** `uv run python -m app.auth.cli create-user <email>` (first user), `reset-password <email>`, `list`. Each prints a temporary password once.
- **Login:** `POST /auth/login {email, password}`. scrypt (stdlib, `n=2^14, r=8, p=1`) via `app/auth/passwords.py`; an unknown email is verified against a dummy hash and every failure returns the same 401 (no user enumeration). On success create an `auth_sessions` row (random 256-bit token, SHA-256 stored), cookie `session=<token>; HttpOnly; Secure; SameSite=Strict; Path=/`.
- **Temporary passwords:** a new or reset user has `must_change_password`. Until `POST /auth/change-password` succeeds every endpoint except `/auth/session`, `/auth/logout`, `/auth/change-password` answers 403 `password_change_required` (`require_session` vs `RawSessionDep`). New passwords: ≥12 characters, different from the current one. Changing a password ends the user's *other* sessions.
- **User management (any signed-in user):** `GET /users`, `POST /users {email}` → `{user, temporaryPassword}` (shown once, only the hash stored), `POST /users/{id}/deactivate | reactivate | reset-password`. Deactivate and reset end that user's sessions; a user cannot deactivate themselves (lockout guard). `authenticate()` also rejects sessions of deactivated users.
- **Session lifetime:** `session_idle_minutes` 60, `session_absolute_hours` 12; bound to the exact User-Agent (OD-23 interpretation unchanged).
- **Step-up:** `POST /auth/step-up {password}` sets `step_up_at`; `require_step_up` accepts if `now - step_up_at ≤ 120 s` and consumes it (one step-up per protected action). Used by card decisions that need it, manual entry, flatten, lot adjust.
- **Rate limiting:** in-process sliding window per client IP on login, step-up and change-password (10/min) → 429 + audit `rate_limited` (TRD §15.2: no Redis).
- **CSRF:** `SameSite=Strict` + `Origin` must equal `dashboard_origin` on state-changing requests (middleware).
- **Audit log:** `audit_log` is append-only (`trading_app` has `SELECT, INSERT` only). Columns: user id + email snapshot, `action`, resource type/id, `ip`, `request_id` (`X-Request-ID` or generated), `details` jsonb. Actions: user_created/deactivated/reactivated, password_reset/changed, login_success/failed, logout, step_up_success/failure, rate_limited (all inside `AuthService`), and user-initiated trading actions written by the routes after success: trade_approved/rejected, card_edited (`/cards/{id}/decision`), order_created / order_cancelled (manual entry, exit, stops, flatten), lot_adjusted, kill_switch. System-originated orders (executor, timers) stay in `orders`/`events`. Passwords and temporary passwords are never logged. `GET /audit?userId&action&cursor&limit` reads it.

## Out of scope
Roles/permissions, self-registration, email delivery of temporary passwords, password-reset-by-email, MFA.

## API contract
| Method | Path | Request | Response | Auth | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | `{email, password}` | `SessionResponse{email, expiresAt, mustChangePassword, stepUpUntil}` + cookie | none | 401, 429 |
| POST | `/api/v1/auth/step-up` | `{password}` | `{stepUpUntil}` | session | 401 wrong password, 429 |
| POST | `/api/v1/auth/change-password` | `{currentPassword, newPassword}` | 204 | session (even while a change is owed) | 401, 422, 429 |
| POST | `/api/v1/auth/logout` | — | 204 | session | — |
| GET | `/api/v1/auth/session` | — | `SessionResponse` | session | 401 |
| GET / POST | `/api/v1/users` | — / `{email}` | `UserOut[]` / `{user, temporaryPassword}` | session | 401, 403, 409 duplicate, 422 |
| POST | `/api/v1/users/{id}/deactivate`, `/reactivate`, `/reset-password` | — | `UserOut` / `UserOut` / `{user, temporaryPassword}` | session | 404, 409 (self) |
| GET | `/api/v1/audit` | `?userId&action&cursor&limit` | `Page[AuditEntry]` | session | 401 |

## Files
`backend/app/auth/{models,repo,service,audit,passwords,sessions,rate_limit,cli}.py`, `backend/app/api/{deps.py,origin_check.py}`, `routes/{auth,users}.py`, `schemas/{auth,users}.py`, migration `0015_password_auth.py` (adds `users`, `audit_log`; `auth_sessions.user_id`; drops `passkey_credentials`, `enrollment_tokens`, `logins`; revokes existing sessions — one-way). `webauthn` dependency and `rp_id` / `rp_name` settings removed.

## Security considerations
Password hashes only; sessions hashed; temporary passwords shown once; uniform login failure; timing-equalised unknown-email path; deactivation kills sessions immediately; audit is append-only at the DB grant level.

## Tests
`tests/unit/auth/test_passwords.py`, `test_sessions.py`; `tests/integration/api/test_auth_api.py` (login/logout, failures look alike, forced change, other-session revoke, UA change, idle, step-up single use/lapse, rate limit, hashed storage), `test_users_api.py` (unauthenticated 401, no register route, temp password once, deactivate/reactivate/reset, self guard, audit filters/pagination, DB-enforced append-only), audit assertions in `test_card_decision_api.py` and `test_manual_api.py`.

## Acceptance criteria
Only allowlisted active users can sign in; temporary passwords must be changed; step-up is a single-use password check; every security-sensitive and user-initiated trading action is audited; no public registration endpoint.

## Open decisions touched
OD-23 (meaning of "device-bound"), unchanged.
