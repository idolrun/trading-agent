# F002 — Local PostgreSQL & Migrations

## Objective
Start PostgreSQL 17 with one documented command, connect the app through SQLAlchemy 2 async + psycopg 3, run Alembic migrations, and give tests a real, isolated PostgreSQL database.

## Requirements covered
- PRD §18 (PostgreSQL sole source of truth), TRD §3 (as amended: PG 17, psycopg 3, Alembic — README §10.1), TRD §8 (runtime role `INSERT, SELECT` only on audit tables), coding-practices §8, §9, §17 (no SQLite).

## Dependencies
- Depends on: F001
- Produces: `compose.yaml` postgres service, roles `trading_owner`/`trading_app`, `app/db.py` (`engine`, `session_factory`, `get_session` dependency, `transaction()`), Alembic async env, migration `0001_roles_and_schemas`, pytest DB fixtures.
- Used by: F003–F005 and every DB feature.

## Scope
Docker → PostgreSQL → application:
```
compose.yaml (postgres:17 pinned to a minor tag, e.g. 17.x-bookworm — resolve current tag)
   ├─ env: POSTGRES_PASSWORD, POSTGRES_USER=postgres (superuser, local only) from repo-root .env
   ├─ volume: pgdata (named) → /var/lib/postgresql/data
   ├─ init: ./infra/postgres/init → /docker-entrypoint-initdb.d (roles + trading_test DB)
   ├─ healthcheck: pg_isready -U postgres -d trading   (interval 5s, retries 10)
   └─ port: ${POSTGRES_PORT:-5432}:5432 bound to 127.0.0.1
```
- Environment variables (all in `.env.example`, real values in untracked `.env`): `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `TRADING_OWNER_PASSWORD`, `TRADING_APP_PASSWORD`, `DATABASE_URL` (`postgresql+psycopg://trading_app:…@127.0.0.1:5432/trading`), `DATABASE_OWNER_URL` (migrations), `TEST_DATABASE_URL`, `TEST_DATABASE_OWNER_URL`.
- Deps: `sqlmodel`, `sqlalchemy[asyncio]`, `alembic`, `psycopg[binary,pool]`.

## Out of scope
Tables (F003–F005). Production managed Postgres (F064). LangGraph schema tables (created by `setup()` in F035; this feature only creates the empty `langgraph` schema).

## Architecture
- `app/db.py`: one `AsyncEngine` created in lifespan (`pool_pre_ping=True`, pool size from settings); `async_sessionmaker(expire_on_commit=False)`; `get_session()` FastAPI dependency (one session per request, never shared across tasks); `transaction(session)` async context manager that commits or rolls back.
- Migrations run as `trading_owner` (`DATABASE_OWNER_URL`); the app runs as `trading_app` (`DATABASE_URL`). Each later migration grants per-table privileges (schema doc §1.3).
- `0001_roles_and_schemas`: `CREATE SCHEMA langgraph AUTHORIZATION trading_owner`; `GRANT USAGE ON SCHEMA public, langgraph TO trading_app`; `ALTER DEFAULT PRIVILEGES … ` **not** used (explicit grants per table keep audit tables insert-only).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `compose.yaml` (repo root) | local Postgres only (the app runs via `uv` in dev) | credentials |
| `.env.example` (repo root) | every env var with placeholder values and comments | real secrets |
| `infra/postgres/init/01-roles.sh` | creates `trading_owner`, `trading_app` (passwords from env), DBs `trading` and `trading_test` owned by `trading_owner` | tables |
| `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako` | async Alembic env reading `DATABASE_OWNER_URL` from `Settings`; `target_metadata = SQLModel.metadata` via `app.db_models` | model definitions |
| `backend/migrations/versions/0001_roles_and_schemas.py` | schema + usage grants | tables |
| `backend/app/db.py` | engine/session/transaction helpers | queries, business logic |
| `backend/app/db_models.py` | imports every module's `models.py` so Alembic autogenerate sees them (empty list now) | definitions |
| `backend/tests/integration/conftest.py` | session-scoped: run `alembic upgrade head` on `trading_test` once; function-scoped `db_session` using `trading_app`; autouse `TRUNCATE … RESTART IDENTITY CASCADE` of all public tables after each test (owner connection) | per-test schema creation |
| `backend/tests/integration/test_db_smoke.py` | connect as app role, `SELECT 1`, verify `langgraph` schema exists | — |

## Files to modify
- `backend/app/config.py` — add DB URLs, pool size.
- `backend/app/main.py` — create/dispose engine in lifespan.
- `backend/pyproject.toml` — deps.
- `backend/README.md` — DB commands.

## Commands (document in backend/README.md)
```bash
cp .env.example .env               # then fill in local passwords
docker compose up -d postgres
docker compose ps                  # wait for "healthy"
cd backend && uv run alembic upgrade head
uv run pytest -m integration
docker compose down                # keep data; `down -v` wipes the volume
```

## Test / seed strategy
- Tests always hit real PG 17 (`trading_test`), migrated once per session; truncation between tests (not transaction rollback) because features rely on commits (`NOTIFY` fires on commit, advisory locks, separate connections).
- No seed data in migrations. Scenario fixtures live in `backend/tests/fixtures/` (added by the features that need them). Runtime config is created by F006's CLI, not seeded.
- CI later: same compose service.

## Error cases
- DB down at startup → lifespan logs and exits non-zero (the webhook's 503 behaviour is F011).
- Wrong role used for migrations → Alembic fails on `CREATE` (intended).

## Security
Credentials only in `.env` (git-ignored); port bound to 127.0.0.1; app role cannot DDL.

## Concurrency
One session per request/task; never share `AsyncSession` across asyncio tasks (coding-practices §5).

## Tests
- Integration: smoke test above; `alembic upgrade head` then `downgrade base` then `upgrade head` succeeds.

## Acceptance criteria
- Fresh clone: `cp .env.example .env` (fill), `docker compose up -d postgres`, `uv run alembic upgrade head` succeed; container reports healthy.
- `uv run pytest -m integration` passes using `trading_test`.
- App role cannot `CREATE TABLE` in `public` (asserted in test).

## Implementation steps
1. Write `.env.example` and `compose.yaml`; write `infra/postgres/init/01-roles.sh`.
2. `docker compose up -d postgres`; verify health.
3. `uv add sqlmodel "sqlalchemy[asyncio]" alembic "psycopg[binary,pool]"`.
4. Extend `Settings`; write `app/db.py`; wire engine into lifespan.
5. `uv run alembic init -t async migrations`; adapt `env.py` to Settings + `app.db_models`.
6. Write migration `0001_roles_and_schemas`.
7. Write integration conftest + smoke test (incl. no-DDL assertion).
8. Document commands in `backend/README.md`.

## Definition of done
Global DoD + acceptance criteria.
