# F001 — Backend Foundation

## Objective
Turn the `backend/` uv stub into a runnable FastAPI service skeleton with typed settings, structured logging, a test harness and lint/type tooling — nothing domain-specific yet.

## Requirements covered
- TRD §3 (Python asyncio, `Decimal` money, pytest + hypothesis), TRD §10 (keys never in logs), coding-practices §2, §4, §16, §19.
- PRD §13 (secrets never in code or logs).

## Dependencies
- Depends on: —
- Produces: `create_app()`, `Settings`, `get_settings()`, JSON logger with redaction, `GET /health` (liveness only), pytest config.
- Used by: every feature.

## Scope
- Dependencies (resolve current versions with `uv add`; see README §12): runtime `fastapi[standard]`, `pydantic-settings`; dev `pytest`, `pytest-asyncio`, `hypothesis`, `httpx`, `ruff`, `mypy`. Move `ruff` from runtime to dev deps (it is currently a runtime dep in `pyproject.toml`).
- Keep existing `langchain*`/`langgraph` deps (used from F031); do not add more yet.
- `pyproject.toml`: project name `trading-agent-backend`, `requires-python = ">=3.13"`, ruff (line length 100, rules `E,F,I,B,UP,ASYNC,S,RUF`), mypy strict for `app/`, pytest `asyncio_mode = "auto"`, markers `integration`, `graph`, `load`.
- Delete `backend/main.py` (hello-world stub).

## Out of scope
Database (F002), auth (F009), any business module.

## Architecture
`app/main.py` exposes `create_app()`; the `lifespan` context is where later features register background services (dispatcher, timers, reconcile). Settings are loaded once at the boundary and injected through `Depends(get_settings)`; no `os.getenv` elsewhere.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/__init__.py` | package | — |
| `backend/app/main.py` | `create_app()`, lifespan skeleton (empty service list), router inclusion, `/health` liveness | business logic |
| `backend/app/config.py` | `Settings(BaseSettings)`: `environment: Literal["development","test","production"]`, `database_url: PostgresDsn` (used from F002), `log_level`, `secret_*` fields as `SecretStr`; `get_settings()` cached | runtime trading config (that is F006 `RuntimeConfig` in the DB) |
| `backend/app/logging.py` | JSON log formatter, `configure_logging()`, redaction filter for `SecretStr` values and keys matching `(?i)(key|token|secret|authorization|cookie)`; `bind(**correlation_ids)` via `contextvars` | print statements |
| `backend/app/api/__init__.py`, `backend/app/api/errors.py` | `ApiError(message, code, status)` + exception handler returning `{"message","code"}` | route logic |
| `backend/app/api/schemas/base.py` | `CamelModel` (alias generator `to_camel`, `populate_by_name`, `serialize_by_alias`), `DiscordId = Annotated[int, PlainSerializer(str)]` | domain types |
| `backend/tests/conftest.py` | `settings` override fixture, `client` fixture (`httpx.AsyncClient(transport=ASGITransport(app))`) | DB fixtures (F002) |
| `backend/tests/unit/test_logging.py`, `backend/tests/api/test_health.py` | tests | — |

## Files to modify
- `backend/pyproject.toml` — deps, tool config (above).
- `backend/README.md` — replace with: purpose, commands (`uv sync`, `uv run pytest`, `uv run fastapi dev app/main.py`, lint/type commands), link to `docs/implementation/README.md`.
- `.gitignore` — confirm `backend/.env` ignored (already `.env`).

## API contract
| Method | Path | Auth | Response | Codes |
|---|---|---|---|---|
| GET | `/health` | none | `{"status": "ok"}` | 200 (F049 extends with heartbeat age → 503) |

## Error cases
Missing required env var → startup fails with a clear pydantic error (no default secrets).

## Security
Secrets only via env / root-only env file (TRD §10); `SecretStr` prevents accidental logging; redaction filter is defence-in-depth.

## Tests
- Unit: redaction filter masks `SecretStr` and `Authorization` headers; `CamelModel` dumps camelCase and serializes `DiscordId` as string.
- API: `GET /health` → 200 JSON.

## Acceptance criteria
- `cd backend && uv sync && uv run pytest` passes; `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app` clean.
- `uv run fastapi dev app/main.py` serves `/health` and `/docs`.
- A log line emitted with a `SecretStr` value never contains the secret.

## Implementation steps
1. Update `pyproject.toml` (name, deps via `uv add`/`uv add --dev`, tool sections); run `uv lock`.
2. Delete `backend/main.py`.
3. Create `app/config.py` with `Settings` + `get_settings`.
4. Create `app/logging.py` (formatter, redaction, contextvar binding).
5. Create `app/api/errors.py` and `app/api/schemas/base.py`.
6. Create `app/main.py` (`create_app`, lifespan, `/health`).
7. Add `tests/conftest.py` and the two tests.
8. Run pytest, ruff, mypy; update `backend/README.md`.

## Definition of done
Global DoD (README §8) + acceptance criteria above.
