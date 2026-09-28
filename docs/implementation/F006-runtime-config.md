# F006 — Runtime Config & Audit

## Objective
One typed, versioned, audited runtime configuration document holding every tunable the PRD names (§18), read by all deterministic code and changeable from the console CLI and (later) the dashboard.

## Requirements covered
- PRD §18 configuration list, C69 (tuned in shadow/paper), §14 (config changes audited), FR-2 (channels, muted authors), FR-15a/C85 (symbol map), C84 (proximity %), FR-1c (staleness), FR-1e (retry limit), TRD §4.4 (thresholds), §4.8 (LLM registry — schema slot only; validation rules added in F031).
- PRD open item (e): **money-related values have no defaults**.

## Dependencies
- Depends on: F004
- Produces: `RuntimeConfig` (Pydantic), `ConfigService.get()/update()`, `missing_for_entries(config)`, in-process cache invalidated by `NOTIFY config`, CLI `app.runtime_config.cli`, `GET/PUT /api/v1/config`.
- Used by: F012, F013, F018, F019, F023, F024, F025, F031, F033, F041–F045, F060.

## Scope — `RuntimeConfig` fields
| Group | Field | Type | Default | Source |
|---|---|---|---|---|
| Mode | `trading_mode` | `shadow | paper | live` | `shadow` | PRD §17 staged go-live (safe default) |
| Feed | `channels` | `list[int]` | `[]` | PRD §18 |
| | `muted_authors` | `list[int]` | `[]` | C57 |
| | `feed_silence_s` | int | **none** | FR-4 |
| | `stale_entry_s` | int | 30 | FR-1c default |
| | `catch_up_backlog_n` | int | **none** (OD-15) | FR-1c |
| | `poison_retry_limit` | int | 3 | FR-1e |
| Context | `context_recent_messages` | int | **none** | C71 "context window size" |
| Sizing | `option_tiers` | `dict[str, Decimal]` (`default`,`small`,`lotto`, extensible) | **none** | C15 |
| | `stock_tiers` | `dict[str, Decimal]` | **none** | C76 |
| Entry validity | `entry_chase_pct` (X) | Decimal | **none** | C22 |
| | `entry_validity_s` (N) | int | **none** | C22 |
| | `entry_cancel_after_s` (M) | int | **none** | C22 |
| Exit | `exit_card_timeout_s` | int | **none** | C22 exit timeout |
| | `exit_reprice_interval_s` | int | **none** | C25 "every few seconds" |
| | `exit_reprice_step_pct` | Decimal | **none** | C25 |
| | `exit_floor_pct` | Decimal | **none** | C25 — **OD-20:** floor definition (proposed: % below the mid at exit start, never below 0.01) |
| Expiry | `cutoff_1_min_before_close`, `cutoff_2_min_before_close` | int | **none** | C19 — **OD-21:** cutoffs expressed as minutes before the session close so half days work (proposal) |
| Risk | `max_daily_loss_usd`, `max_open_positions`, `max_total_exposure_usd` | Decimal/int | **none** | FR-18 |
| Validation | `identity_price_tolerance_pct` | Decimal | **none** | FR-11 |
| | `adjacent_strike_ratio` | Decimal | **none** | FR-13 |
| | `symbol_map` | `dict[str, str]` | `{"SPX": "SPXW"}` | C85 |
| | `missing_underlying_proximity_pct` | Decimal | 10 | TRD §6 default |
| | `missing_underlying_lookback_days` | int | **none** | C84 "recently traded" |
| Routing | `t_chat`, `t_field`, `t_link` | Decimal | 0.9 / 0.8 / 0.8 | TRD §4.4 start values |
| Models | `jev` (`model`, `timeout_ms`, `enabled`) | object | `enabled=false` | TRD §3 |
| | `llm` (roles → chain, prices, params) | object | empty chain | TRD §4.8 (validated in F031) |
| | `note_guard_enabled` | bool | true | OD-6 (TRD recommendation; confirm) |
| | `llm_monthly_budget_usd` | Decimal | **none** | PRD (d) |

`missing_for_entries(cfg) -> list[str]` returns every **none**-default field required to create an approvable entry card. F024 turns a non-empty list into a disabled card "config incomplete: …".

## Out of scope
Frontend screen (F060). LLM chain validation logic (F031 adds a validator hook).

## Architecture
- Document stored in `config.document` (single row, `version`); every change inserts `config_changes` (full doc + JSON diff + actor + reason) in the same transaction T8.
- `ConfigService.get()` returns a cached immutable `RuntimeConfig`; a `LISTEN config` task refreshes the cache on change (single process now; works multi-process later).
- Update uses optimistic concurrency: `PUT` must send `expectedVersion`; mismatch → 409.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/runtime_config/schema.py` | `RuntimeConfig` and nested models, `missing_for_entries` | DB access |
| `backend/app/runtime_config/repo.py` | read row, update row + insert change (T8) | validation rules |
| `backend/app/runtime_config/service.py` | `get()`, `update(doc, expected_version, actor, reason, session_id)`, cache + LISTEN refresh | HTTP |
| `backend/app/runtime_config/cli.py` | `show`, `set --file config.json --reason …` (console bootstrap, actor `cli`) | — |
| `backend/app/api/routes/config.py` | thin routes | business rules |
| `backend/app/api/schemas/config.py` | `ConfigResponse{version, document, missingForEntries}`, `UpdateConfigRequest{expectedVersion, document, reason}` | — |
| `backend/tests/unit/runtime_config/test_schema.py`, `backend/tests/integration/runtime_config/test_service.py`, `backend/tests/api/test_config_api.py` | tests | — |
| `backend/config.example.json` | example document with placeholders + comments in README | real money values |

## Files to modify
`backend/app/main.py` (start config listener in lifespan; include router).

## API contract
| Method | Path | Request | Response | Auth | Service | Errors |
|---|---|---|---|---|---|---|
| GET | `/api/v1/config` | — | `ConfigResponse` | session (F009; until F009 lands, route is registered but protected by a placeholder dependency that denies in production) | `ConfigService.get` | 404 `not_found` if never initialized |
| PUT | `/api/v1/config` | `UpdateConfigRequest` | `ConfigResponse` | session | `ConfigService.update` | 409 `conflict` version mismatch; 422 `invalid` schema errors |

## Error cases
No config row → pipeline treats all entries as "config incomplete"; dispatcher still runs (exits/stops still carded).

## Security
Config contains no secrets (API keys stay in `Settings`/env). Changes audited with session id.

## Tests
- Unit: defaults exactly as table; unknown keys rejected (`extra="forbid"`); `missing_for_entries` lists each unset money field; negative/zero tiers rejected.
- Integration: update writes `config_changes` with diff; stale `expected_version` raises conflict; cache refreshes after NOTIFY.
- API: GET/PUT happy path, 409, 422.

## Acceptance criteria
- A fresh system has no approvable-entry config; `missing_for_entries` names every PRD §18 money value.
- Every change is one `config_changes` row; concurrent updates cannot both win.

## Implementation steps
1. `schema.py` with models, validators, `missing_for_entries`.
2. `repo.py` + `service.py` (T8, cache, LISTEN).
3. CLI `show`/`set`.
4. API schemas + routes.
5. Tests; `config.example.json`.

## Open decisions touched
OD-6, OD-15, OD-20 (exit floor definition), OD-21 (cutoff representation), PRD (d), (e).

## Implementation notes (2026-09-28)
- `missing_for_entries` = **every** unset no-default field (user decision, conservative); no config row counts as all unset.
- Extra validation: `cutoff_1_min_before_close > cutoff_2_min_before_close`; tiers non-empty with positive values; pct fields in (0, 100]; thresholds in [0, 1]; Discord IDs in `channels`/`muted_authors` are serialized as strings.
- `jev.timeout_ms` defaults to 300 (OD-8 p95 budget); `llm` holds only `chains` for now — F031 adds prices/params and validation.
- API tests need the database and lifespan, so they live in `tests/integration/api/test_config_api.py` (not `tests/api/`). CLI is tested in a fresh interpreter (`tests/integration/runtime_config/test_cli.py`).
- `create_app(settings=None)` accepts injected settings (tests); `app.db.create_session_factory` imports `app.db_models` so every process registers all tables before an ORM flush.
- Auth placeholder `require_session` (`app/api/deps.py`) until F009.
