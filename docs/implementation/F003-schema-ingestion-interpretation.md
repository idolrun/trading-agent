# F003 — Schema: Ingestion & Interpretation

## Objective
Create the tables for message transport and interpretation audit exactly as specified in [`database-schema.md` §3](database-schema.md#3-ingestion-and-interpretation-tables-f003), with DB-level constraints proven by tests.

## Requirements covered
- PRD FR-1 (exactly once per message event), FR-1a–FR-1e (inbox statuses, flags), FR-6/FR-7 (signals + provenance), PRD §14 (message events, processing status, signals, links, LLM calls), C63 (append-only).
- TRD §8, §8.1, §2.4 (inbox index).

## Dependencies
- Depends on: F002
- Produces: SQLModel tables `MessageEvent`, `InboxItem`, `StageTiming`, `DispatcherHeartbeat`, `ModelCall`, `Signal`, `MessageLink`; migration `0002_ingestion_interpretation`.
- Used by: F011–F017, F021, F026, F031–F035, F048, F049.

## Scope
Tables: `message_events`, `inbox`, `stage_timings`, `dispatcher_heartbeat`, `model_calls`, `signals`, `message_links`. `message_links.provider_position_id` FK is added in F005 (target table does not exist yet) — create the column now, add the composite FK in `0004`.

## Out of scope
Any repository or service (F011+).

## Data model
Exactly per schema doc §3 (columns, CHECKs, UNIQUEs, indexes). Class: `message_events`, `stage_timings`, `model_calls`, `signals`, `message_links` = **immutable audit**; `inbox`, `dispatcher_heartbeat` = **current state**.

## Database migration
`0002_ingestion_interpretation.py` (hand-reviewed autogenerate):
- create tables + indexes (partial indexes written explicitly with `postgresql_where`);
- grants: audit tables `GRANT SELECT, INSERT … TO trading_app`; current-state `GRANT SELECT, INSERT, UPDATE`;
- identity sequences: `GRANT USAGE ON SEQUENCE` for identity columns.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/inbox/models.py` | `MessageEvent`, `InboxItem`, `StageTiming`, `InboxStatus`/`EventType`/`Stage` `StrEnum`s | queries |
| `backend/app/system/models.py` | `DispatcherHeartbeat` | — |
| `backend/app/llm/models.py` | `ModelCall`, `ModelProvider`, `ModelRole`, `CallOutcome` enums | client code |
| `backend/app/interpret/models.py` | `Signal`, `MessageLink`, `MsgType`, `LinkOutcome`, `LinkMethod` enums | parsing logic |
| `backend/migrations/versions/0002_ingestion_interpretation.py` | DDL + grants | data |
| `backend/tests/integration/schema/test_ingestion_constraints.py` | constraint tests | — |

## Files to modify
- `backend/app/db_models.py` — import the new models modules.

## Concurrency / idempotency
- PK `(message_id, event_type, event_seq)` is the transport idempotency key.
- `signals` UNIQUE `(message_id, event_type, event_seq, idx)` + deterministic `signal_id`.
- `UNIQUE (signal_id, provider_id)` prepares the no-cross-provider composite FK.

## Tests (integration, real PG)
- Duplicate `message_events` insert with `ON CONFLICT DO NOTHING` inserts one row.
- `event_type` outside the set, `event_seq < 0`, `text IS NULL` on a create → `CheckViolation`.
- `inbox.status` invalid → violation; `attempts < 0` → violation.
- As `trading_app`: `UPDATE message_events` and `DELETE FROM signals` → `InsufficientPrivilege`; `UPDATE inbox` allowed.
- `message_links` with `outcome='linked'` and NULL position → violation.

## Acceptance criteria
- Migration up/down/up clean; all tests pass; `EXPLAIN` of `next_for_provider` query (written in test) uses the partial index.

## Implementation steps
1. Write enums + SQLModel classes (jsonb/arrays via `sa_column`, composite PK via `primary_key=True` on each field, CHECKs in `__table_args__`).
2. Import in `db_models.py`; autogenerate `0002`; hand-edit partial indexes, grants, identity sequence grants.
3. Upgrade; write constraint + privilege tests.
4. Verify index usage with `EXPLAIN` in a test.

## Definition of done
Global DoD; schema matches `database-schema.md` §3 column-for-column.
