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

`ENVIRONMENT=test` swaps in a controllable clock and mounts the E2E control plane
(`/api/v1/test/*`, `app/api/routes/testing.py`); never set it outside the browser E2E suite
(`frontend/e2e/README.md`, `cd ../frontend && pnpm e2e`).

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

## Password auth (F009)

Email + password, allowlist only: there is no self-registration and no public register
endpoint. The first user (and any recovery) comes from the server console:

```bash
uv run python -m app.auth.cli create-user you@example.com    # prints a temporary password once
uv run python -m app.auth.cli reset-password you@example.com # new temporary password, ends sessions
uv run python -m app.auth.cli list
uv run python -m app.auth.cli seed-dev-user                  # ENVIRONMENT=development only
```

`seed-dev-user` creates (or resets) the local desk login `dev@idolrun.com` with the fixed
development password in `app/auth/cli.py` (`DEV_PASSWORD`), no forced change. It refuses to run
in any other environment.

Temporary passwords must be changed at first login (`POST /api/v1/auth/change-password`; every
other endpoint answers 403 `password_change_required` until then). A signed-in user manages
users through `/api/v1/users` (add, deactivate, reactivate, reset password) and reads
`/api/v1/audit`. Sessions: HttpOnly Secure SameSite=Strict cookie, 60 min idle / 12 h
absolute, bound to the browser's User-Agent. State-changing `/api/*` calls must carry
`Origin: DASHBOARD_ORIGIN`. Card decisions, manual entry, flatten and lot adjust need a fresh
password step-up (`/auth/step-up`, 120 s, single use).

## Discord listener

The backend reads Discord itself (a user-account "selfbot" via `discord.py-self`: against
Discord's terms, use a throwaway account). Set `DISCORD_TOKEN` in `.env` and restart; unset =
listener off. Watched channels are `channels` in the runtime config, managed on the dashboard
**Discord** page (or `/config`). Messages, edits and deletes go through the same inbox path as
`POST /ingest`; gap backfill uses the channel history after a restart. Bot authors are ignored;
embed text is appended to the message. `GET /api/v1/discord/status` reports the connection.

## Interpretation order

1. **Jev** (TypeSafe) reads every message. What it settles with confidence is decided at once
   (feed-only or an assembled signal).
2. The **LLM extraction agent** is **optional**: it runs only when a chain entry in
   `llm.roles.extract` has a provider with an API key. Without one, a message nothing settled
   becomes a review card whose reason says why (`needs_review: msg_type: below t_type`,
   `needs_review: jev_unavailable`), and no "parser degraded" flag is raised. With an LLM
   configured, a slow or failing chain still raises `parser_degraded` as before. LLM calls take
   seconds (the default `extract` deadline is 3000 ms and a slower provider will exhaust it), so
   size `deadline_ms` to the provider you use.

Laya (the local `laya-mlx` fallback) was removed in F098: a Jev model failure goes straight to
the LLM, else review. Old rows may still carry provider/interpreter/link method `laya`.

## Message ingest (F011)

`POST /ingest` takes the (provisional) Message API payload, signed with
`X-Timestamp: <unix s>` and `X-Signature: hex(HMAC-SHA256(INGEST_HMAC_SECRET, "<ts>.<raw body>"))`
(±300 s). 200 `{"accepted": true|false}` only after commit (false = duplicate); 401 bad
signature; 422 unmappable payload (+ alert); 503 when the database is unavailable.
