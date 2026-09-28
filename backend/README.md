# Trading Agent — Backend

FastAPI service for the Discord signal copy-trading assistant. Roadmap and feature specs:
[`docs/implementation/README.md`](../docs/implementation/README.md). Engineering rules:
[`coding-practices.md`](coding-practices.md).

## Commands

```bash
uv sync                                   # install locked deps
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run fastapi dev app/main.py            # dev server: /health, /docs
```

Settings come from environment variables or the repo-root `.env` (see `app/config.py`).

## Local database (PostgreSQL 17 in Docker)

```bash
cp .env.example .env               # from repo root, then fill in local passwords
docker compose up -d postgres      # from repo root
docker compose ps                  # wait for "healthy"
cd backend && uv run alembic upgrade head
uv run pytest -m integration       # uses database trading_test
docker compose down                # keeps data; `down -v` wipes the volume
```

- Roles: `trading_owner` runs migrations (`DATABASE_OWNER_URL`); the app runs as `trading_app`
  (`DATABASE_URL`, DML only). Both are created by `infra/postgres/init/01-roles.sh` on first
  start with an empty volume — changing passwords later needs `docker compose down -v`.
- Port 5432 taken by another project? Set `POSTGRES_PORT` and the port in the four URLs in `.env`.
- New migration: `uv run alembic revision --autogenerate --rev-id 000N -m "..."`, then hand-review.

## Runtime config (F006)

Trading tunables live in the database (`config` + audited `config_changes`), not in env vars.
Money values have no defaults: until every one is set, entry cards are disabled ("config
incomplete"). Start from `config.example.json` (defaults filled, money fields `null`).

```bash
uv run python -m app.runtime_config.cli show
uv run python -m app.runtime_config.cli set --file config.json --reason "initial tiers"
```

API: `GET /api/v1/config`, `PUT /api/v1/config` with `{expectedVersion, document, reason}`
(409 on a stale version); both need a signed-in session.

## Passkey auth (F009)

Passkey only — no password path exists. New passkeys can only be added from the server console:

```bash
uv run python -m app.auth.cli enroll            # one-time token, valid 15 min
uv run python -m app.auth.cli list
uv run python -m app.auth.cli revoke <credential-id>   # also ends its sessions
```

The dashboard registers with the token (`/api/v1/auth/register/*`), then signs in
(`/api/v1/auth/login/*`). Sessions: HttpOnly Secure SameSite=Strict cookie, 60 min idle /
12 h absolute, bound to the browser's User-Agent. State-changing `/api/*` calls must carry
`Origin: DASHBOARD_ORIGIN`. Manual entry / flatten need a fresh step-up (`/auth/step-up/*`,
120 s, single use).
