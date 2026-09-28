# F004 — Schema: Platform (alerts, config, flags, auth, push, evaluation)

## Objective
Create platform tables per [`database-schema.md` §5](database-schema.md#5-platform-tables-f004). They come before trading state because `approvals` references `auth_sessions` and `provider_position_events` references `manual_corrections`.

## Requirements covered
- PRD §13 (passkey-only, sessions, login audit), §14 (alerts, config changes, manual corrections, logins, eval labels/runs), FR-26 (push), TRD §9 (entry block), 7.9 (kill switch), FR-1c (catch-up), FR-4 (degraded), C64, C67, C73.

## Dependencies
- Depends on: F003
- Produces: tables `alerts`, `alert_acks`, `config`, `config_changes`, `system_flags`, `system_flag_events`, `manual_corrections`, `logins`, `passkey_credentials`, `enrollment_tokens`, `auth_sessions`, `push_subscriptions`, `eval_labels`, `eval_runs`; FK `model_calls.eval_run_id → eval_runs`; migration `0003_platform`.
- Used by: F006, F008, F009, F029, F047, F048.

## Scope / data model
Exactly per schema doc §5. Classes: audit = `alerts`, `alert_acks`, `config_changes`, `system_flag_events`, `manual_corrections`, `logins`, `eval_labels`, `eval_runs`; current = `config`, `system_flags`, `passkey_credentials`, `enrollment_tokens`, `auth_sessions`, `push_subscriptions`.
Migration also inserts the five `system_flags` rows (`active=false`) — structural rows, not config values.

## Out of scope
Services (F006, F008, F009, F029, F048).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/alerts/models.py` | `Alert`, `AlertAck`, `Severity`, `AlertKind` | alert policy |
| `backend/app/system/models.py` (extend) | `SystemFlag`, `SystemFlagEvent`, `FlagName` | — |
| `backend/app/runtime_config/models.py` | `ConfigRow`, `ConfigChange` | the `RuntimeConfig` schema (F006) |
| `backend/app/manual/models.py` | `ManualCorrection` | — |
| `backend/app/auth/models.py` | `Login`, `PasskeyCredential`, `EnrollmentToken`, `AuthSession` | WebAuthn logic |
| `backend/app/notify/models.py` | `PushSubscription` | — |
| `backend/app/evalkit/models.py` | `EvalLabel`, `EvalRun` | — |
| `backend/migrations/versions/0003_platform.py` | DDL, grants, flag rows, FK on `model_calls` | config values |
| `backend/tests/integration/schema/test_platform_constraints.py` | tests | — |

## Files to modify
`backend/app/db_models.py`.

## Security
`auth_sessions` stores only a hash of the session token; `enrollment_tokens` only a hash; credentials' public keys only.

## Tests
- `config` rejects `id <> 1`; `system_flags` rejects unknown flag names; five flag rows exist after migration.
- App role cannot UPDATE/DELETE `alerts`, `logins`, `config_changes`, `manual_corrections`.
- `alert_acks` second ack for the same alert → unique violation.
- `eval_labels.split` outside `dev|test` → violation.

## Acceptance criteria
Migration up/down/up clean; tests pass; schema matches doc §5.

## Implementation steps
1. Models + enums. 2. Autogenerate `0003`, add grants, flag rows, `model_calls` FK. 3. Tests. 4. Up/down/up.

## Definition of done
Global DoD.
