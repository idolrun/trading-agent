# F009 — Passkey Auth & Sessions (backend)

## Objective
Passkey-only authentication for the single user: console enrolment, login, short server-side sessions, step-up confirmation, rate limiting and a login audit — and the `require_session` / `require_step_up` dependencies every protected route uses.

## Requirements covered
- PRD §13 (public dashboard, passkey-only, no password, recovery only via server console, short device-bound sessions, login audit, rate limiting, step-up for manual entry and flatten, single user), AC-10, C39, C42.
- TRD §10 (server-side sessions, `SameSite=Strict` cookies).

## Dependencies
- Depends on: F004
- Produces: `/api/v1/auth/*` endpoints, `require_session`, `require_step_up`, `AuthContext(session_id, credential_id)`, CLI `app.auth.cli`.
- Used by: every `/api/v1` route; F036 (approvals record `session_id`), F047 (step-up), F053 (UI).

## Scope
- **Enrolment (console only):** `uv run python -m app.auth.cli enroll --label "phone"` creates an `enrollment_tokens` row (hash stored, raw token printed once, 15 min expiry). Registration endpoints require that token. `cli revoke <credential>`; `cli list`. No other way to add a credential (recovery = console).
- **Login:** WebAuthn authentication (`userVerification="required"`); on success create `auth_sessions` row (random 256-bit token, store SHA-256), set cookie `session=<token>; HttpOnly; Secure; SameSite=Strict; Path=/`.
- **Session lifetime:** `session_idle_minutes` and `session_absolute_hours` in `Settings` (proposal: 60 min idle, 12 h absolute — covers one US session 19:15–01:45 local). **"Device-bound"** interpreted as: session bound to the credential used and to the User-Agent hash; a UA change revokes it (OD-23: confirm interpretation).
- **Step-up:** `POST /auth/step-up/options|verify` — fresh assertion sets `step_up_at`; `require_step_up` accepts if `now - step_up_at ≤ 120 s` and consumes it (one step-up per protected action).
- **Rate limiting:** in-process sliding window per client IP on `/auth/*` (e.g. 10/min) → 429 + `logins(outcome='rate_limited')` (TRD §15.2: no Redis).
- **CSRF:** `SameSite=Strict` + `Origin` header must equal the configured dashboard origin on every state-changing request (middleware).
- **Audit:** every attempt → `logins` row (success/failure/step-up/logout/rate_limited/enrolled).

## Out of scope
Login UI (F053). Multi-user/roles (PRD non-goal).

## API contract
| Method | Path | Request | Response | Auth | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/register/options` | `{enrollmentToken, label}` | WebAuthn creation options JSON | enrolment token | 401 invalid/expired token |
| POST | `/api/v1/auth/register/verify` | `{enrollmentToken, credential}` | `{credentialId}` | token | 422 verification failed |
| POST | `/api/v1/auth/login/options` | — | request options JSON (challenge stored server-side, 2 min TTL) | none | 429 |
| POST | `/api/v1/auth/login/verify` | `{credential}` | `SessionResponse{expiresAt}` + cookie | none | 401, 429 |
| POST | `/api/v1/auth/step-up/options` / `verify` | — / `{credential}` | options / `{stepUpUntil}` | session | 401 |
| POST | `/api/v1/auth/logout` | — | 204 | session | — |
| GET | `/api/v1/auth/session` | — | `SessionResponse` | session | 401 |

Challenges are stored in an in-process TTL dict keyed by a random id sent as an HttpOnly cookie (single process; ponytail: move to a table if multi-process).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/auth/webauthn_service.py` | wraps `webauthn` lib: options + verification, sign-count check | HTTP/cookies |
| `backend/app/auth/sessions.py` | create/validate/revoke sessions, step-up, idle/absolute expiry | WebAuthn |
| `backend/app/auth/rate_limit.py` | sliding window limiter | — |
| `backend/app/auth/repo.py` | credentials, tokens, sessions, logins queries | policy |
| `backend/app/auth/cli.py` | enroll / list / revoke | — |
| `backend/app/api/deps.py` | `require_session`, `require_step_up`, `get_session` re-export | business logic |
| `backend/app/api/routes/auth.py`, `backend/app/api/schemas/auth.py` | thin routes, schemas | — |
| `backend/app/api/origin_check.py` | Origin-header middleware | — |
| `backend/tests/unit/auth/…`, `backend/tests/api/test_auth_api.py` | tests (WebAuthn responses produced with a software authenticator helper in tests) | — |

## Files to modify
`app/config.py` (`rp_id`, `rp_name`, `dashboard_origin`, session timings); `app/main.py` (router, middleware); F006 config routes switch to `require_session`.

## Security considerations
No password path exists anywhere (AC-10). Session tokens never logged. Enrolment token single use. Sign-count regression → reject + urgent alert (cloned authenticator).

## Tests
- Unit: session expiry idle/absolute; step-up window and single use; rate limiter.
- API: full register → login → session → logout with a software authenticator; expired enrolment token → 401; wrong origin → 403; protected route without cookie → 401; step-up-protected dummy route without step-up → 403 `step_up_required`.
- Static test: no route accepts a `password` field (scan OpenAPI).

## Acceptance criteria
AC-10 backend part: passkey-only; step-up dependency works; every attempt audited.

## Implementation steps
1. `uv add webauthn`. 2. Repo. 3. WebAuthn service. 4. Sessions + step-up. 5. Rate limiter + origin middleware. 6. Deps. 7. Routes. 8. CLI. 9. Tests.

## Open decisions touched
OD-23 (meaning of "device-bound").

## Implementation notes (2026-09-28)
- Extra file `app/auth/service.py` (`AuthService`): orchestration of challenge store + WebAuthn + repo + audit, so routes stay thin. Challenge store lives in `webauthn_service.py`.
- OD-23 proposal implemented (session bound to credential + exact User-Agent; change revokes) — still pending confirmation.
- Rate limit (10/min per peer IP) applies to the POST login/register/step-up endpoints, not to `GET /auth/session` or logout. Peer IP is the direct client; the reverse-proxy header is F064's job.
- `enroll` takes no `--label`; the label is given at registration (`register/options`).
- Sign-count regression is detected from py_webauthn's error text (it has no dedicated exception); raises urgent `auth_anomaly` alert (migration `0006`).
- Origin middleware returns 403 `forbidden` (new error code, README §1.2).
- Tests use a software authenticator (`tests/webauthn_authenticator.py`, ES256, attestation `none`); API tests live in `tests/integration/api/` (need DB + lifespan). F006 config routes now require a session; the dev placeholder is gone.
