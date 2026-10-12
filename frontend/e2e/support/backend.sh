#!/bin/sh
# E2E backend (playwright.config.ts webServer): recreate + migrate the dedicated database, then
# serve with ENVIRONMENT=test (fakes, controllable clock, /api/v1/test/*). Env comes from
# e2e/support/env.ts.
set -eu
cd "$(dirname "$0")/../../../backend"

uv run python - <<'PY'
import os

import psycopg

with psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=os.environ["POSTGRES_PORT"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    dbname="postgres",
    autocommit=True,
) as conn:
    name = os.environ["E2E_DB"]
    # Fresh every run: a dead run's due timers would otherwise fire at startup against the new,
    # empty fake broker ("unknown order") before global-setup resets the database.
    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{name}" OWNER trading_owner')  # roles: 01-roles.sh
    print(f"created database {name}")
PY
uv run alembic upgrade head
uv run python -m app.graph.checkpointer setup
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" --log-level warning
