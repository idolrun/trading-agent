#!/bin/bash
# Runs once, on first start with an empty volume. Creates the two roles (database-schema.md §1.3)
# and the app + test databases, both owned by trading_owner. Tables come from Alembic.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$TRADING_OWNER_PASSWORD" -v app_pw="$TRADING_APP_PASSWORD" <<'SQL'
CREATE ROLE trading_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE trading_app LOGIN PASSWORD :'app_pw';
CREATE DATABASE trading OWNER trading_owner;
CREATE DATABASE trading_test OWNER trading_owner;
SQL
