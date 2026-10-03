# F064 — Deployment, Runbooks & Stage Gates

## Objective
Deploy the system safely (app VM + managed HA PostgreSQL 17), document operator runbooks, and run the staged go-live with measurable gates before real money.

## Requirements covered
- Decision log: C34, C37.
- PRD §17 staged go-live (replay → shadow ≥ 20 days → paper ≥ 10 days → live at minimum sizes ≥ 10 days), §18 (DB not on the app server/disk; automatic failover; PITR; runbooks for recovery, reconciliation mismatches, password recovery, kill switch), §12 durability (RPO ≈ 0 critical data, RTO ≤ 5 min), §13 (secrets storage), AC-2 (audit query in paper/live), AC-12, AC-23.
- TRD §3 hosting (one US-East VM, Docker Compose app + Caddy, external uptime monitor; managed Postgres HA in same region), §9, §10.

## Dependencies
- Depends on: F062, F063 (and F050, F051 unblocked before paper)
- Produces: `backend/Dockerfile`, `deploy/compose.prod.yaml` (backend, frontend, Caddy), `deploy/Caddyfile`, secrets layout, runbooks, stage-gate checklists.

## Scope
- **Images:** backend (uv-based multi-stage, non-root), frontend (`next build` standalone). Pin base image digests.
- **Compose prod:** backend + frontend + Caddy (TLS, rate limiting on `/api/v1/auth/*`, `/ingest` exposed, security headers). No Postgres container in prod.
- **Exactly one backend instance** (F076): `uvicorn --workers 1`, `replicas: 1`, no auto-scaling. Lane ordering, the account entry gate and bracket locks are in-process. A second instance on the same database is refused at startup (`SecondInstance`, Postgres advisory lock held on its own connection; released automatically if the process dies).
- **Postgres:** managed provider (OD-9) with HA/sync standby and PITR; private network path (OD-17); `DATABASE_URL` via root-only env file; migrations run as owner in a release step; `graph setup` once.
- **Secrets:** root-only env file (TRD §10) for broker, LLM, TypeSafe, VAPID, HMAC; never in images or logs.
- **Monitoring:** external uptime monitor on `/health` (heartbeat-aware) paging the phone (TRD §9 known gap); alerts via Web Push.
- **Runbooks** (`docs/runbooks/`): recovery after crash/failover (startup sequence checks), reconciliation mismatch resolution, password recovery (console `app.auth.cli create-user` / `reset-password`), kill switch/flatten, Message API outage, LLM outage, market-data outage.
- **Stage gates** (checklists in `docs/runbooks/stage-gates.md`, results recorded):
  1. Replay: F048 gate passed per model in chain (PRD §17.1).
  2. Shadow ≥ 20 trading days: zero invariant violations, latency targets incl. bursts, zero undetected feed outages, zero lost messages; F063 passed before start.
  3. Paper ≥ 10 days: every order path exercised (entry, bracket, trim, add, broker stop, underlying stop, stop restore, EOD forced close, crash recovery); zero unexplained reconciliation mismatches; AC-2 audit query empty.
  4. Live minimum sizes ≥ 10 days before size increases.
- `trading_mode` switches (`shadow` → `paper` → `live`) only via config change with reason, audited.

## Files to create
`backend/Dockerfile`, `frontend/Dockerfile`, `deploy/compose.prod.yaml`, `deploy/Caddyfile`, `deploy/README.md`, `docs/runbooks/*.md`, `docs/runbooks/stage-gates.md`.

## Acceptance criteria
Deployed staging reproduces F062/F063 results; failover drill meets RTO ≤ 5 min with reconciliation before entries (AC-23); runbooks reviewed; stage gates recorded in order (AC-12).

## Implementation steps
1. Dockerfiles. 2. Prod compose + Caddy. 3. Managed PG + network. 4. Secrets + monitoring. 5. Runbooks. 6. Staging drill. 7. Stage gates.

## Open decisions touched
OD-9, OD-16, OD-17.
