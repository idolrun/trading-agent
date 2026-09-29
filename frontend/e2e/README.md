# Browser E2E (F062)

Playwright (Chromium) against the whole stack: a production Next build, the FastAPI backend with
`ENVIRONMENT=test` (fake broker / market data / Message API history, recorded interpretations,
controllable clock) and PostgreSQL.

## Run

```sh
open -a Docker                      # macOS, if Docker is not running
docker compose up -d postgres       # repo root; credentials from the repo-root .env
cd frontend
pnpm install
pnpm exec playwright install chromium   # once
pnpm e2e                            # ~40 s, including the Next build
pnpm e2e corrections                # one file; add --headed / --ui / --debug as needed
```

`pnpm e2e` starts both servers itself (`playwright.config.ts` `webServer`) and stops them after:

| Server | Command | Where |
|---|---|---|
| Backend | `sh e2e/support/backend.sh` | `http://127.0.0.1:8100` |
| Dashboard | `next build && next start --port 3100` (build dir `.next-e2e`) | `http://localhost:3100` |

The ports differ from dev (3000 / 8000) so a running `next dev` or dev backend is never reused or
clobbered. The dashboard origin is `http://localhost:3100` (not 127.0.0.1): the backend's Origin
check and the `SameSite=Strict`, `Secure` session cookie (Chromium accepts it on localhost) rely
on it.

## Database

`backend.sh` creates the dedicated database `trading_e2e` (as the Postgres superuser from `.env`:
`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_HOST`/`POSTGRES_PORT`, owner `trading_owner`, like
`infra/postgres/init/01-roles.sh`), runs `alembic upgrade head` and the LangGraph
`checkpointer setup`, then serves. `DATABASE_URL` / `DATABASE_OWNER_URL` are the `.env` ones
pointed at `trading_e2e` (`e2e/support/env.ts`); the shared `trading_test` is never touched.
Environment variables override `.env` (CI).

`global-setup.ts` empties the database (users too) and provisions the desk user with
`uv run python -m app.auth.cli create-user`, replacing its temporary password through the API.
Every test then starts with `POST /api/v1/test/reset` (all tables except users, flags cleared,
fake broker and market data cleared, runtime config seeded from `CONFIG` in `driver.ts`, a port
of `backend/tests/decide_helpers.py`) and signs in through the login form.

## Test-only backend routes

`backend/app/api/routes/testing.py`, mounted only when `ENVIRONMENT=test` (404 in development and
production, asserted by `backend/tests/api/test_testing_routes.py`) and not in the OpenAPI contract:

| Route | Does |
|---|---|
| `GET/POST /api/v1/test/clock` | read / set (`at`) / move (`advanceS`) the app clock; wakes the timer loop |
| `POST /api/v1/test/market` | load `MarketFixture`s, script option quotes (the shadow broker fills at them) |
| `PUT /api/v1/test/interpretations/{messageId}` | the `ParsedSignals` the FixtureInterpreter returns for that message |
| `POST /api/v1/test/reset` | the per-test reset above; refuses if `DATABASE_OWNER_URL` is not the app's database |

Non-GET calls still need `Origin: http://localhost:3100`.

## Writing a scenario

`e2e/support/driver.ts` exports `test` (fixtures `driver` + a signed-in `page`) and the driver:

- `driver.post(text, signals, { author, at })` records the interpretation, then delivers a
  signed `message.create` webhook to `/ingest` (HMAC over `"<ts>.<body>"`, timestamp = app
  clock); `driver.edit(...)` sends a `message.update`. Snowflake ids encode the post time.
- `optionEntry(...)` / `followUp(...)` / `lit(...)` build `ParsedSignal`s; every literal's raw
  text must appear in the message or the pipeline turns it into a review card.
- `driver.clockTo(at)` / `driver.advance(s)` move the app clock (then a Message API heartbeat,
  unless `{ heartbeat: false }`) and the browser's `Date` (`page.clock`).
- `driver.cards() / card(id) / orders() / lots() / positions() / alerts() / flags()` read the
  API with the signed-in session; `driver.waitFor(...)` polls until the pipeline has acted.
  Assert these database side effects, not only what the page says.
- `test.use({ start })` changes the app time the test begins (and signs in) at. Sessions idle
  out after 60 min of app time: keep single clock jumps under that.

The shadow broker (default trading mode) fills a marketable order at submit time; resting orders
stay working.
