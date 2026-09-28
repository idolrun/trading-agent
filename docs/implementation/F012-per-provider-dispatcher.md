# F012 — Per-Provider Dispatcher

## Objective
Process each provider's message events one at a time in post-time order, different providers in parallel, with staleness classification, muted/unconfigured skipping, poison handling and a heartbeat — calling a pluggable `Processor` (a stub now; the LangGraph message graph in F035).

## Requirements covered
- Decision log: C54, C88, C92.
- PRD FR-1a (ordered per provider by post time; providers parallel; approvals never block), FR-1c (message age = processing start − post time), FR-1e (3 failures → raw review card + alert; provider continues), FR-2 (muted authors, configured channels), AC-18 (A3 never processed against a book without A1), AC-20.
- TRD §2.4 (per-provider sequencing, provider slot, poison, watchdog heartbeat every 5 s), §9 step 6 (reset `processing` → `received`).

## Dependencies
- Depends on: F011, F008, F006
- Produces: `Dispatcher` (lifespan service), `Processor` Protocol, `ProcessingContext`, `PoisonHandler` Protocol, heartbeat writer.
- Used by: F013, F035 (real processor), F026 (poison → raw review card handler), F037 (late flag).

## Scope
```python
class ProcessingContext(BaseModel): envelope: Envelope; attempt: int; started_at: datetime
    age_s: float; stale: bool      # age_s > config.stale_entry_s  (FR-1c; the processor decides what stale means per type)
    late: bool                     # a later-posted event of this provider is already done/stale (FR-1b flag; re-linking is F037)
class Processor(Protocol):
    async def process(self, ctx: ProcessingContext) -> None   # must mark inbox done/stale itself inside its apply tx
class PoisonHandler(Protocol):
    async def on_poison(self, ctx: ProcessingContext, error: str) -> None
```
Worker loop per provider:
1. `LISTEN inbox` wakes the provider's worker (spawn if absent); idle workers exit after 60 s without work.
2. Hold the provider slot (`asyncio.Lock` per provider) for the whole `process()` call. The real processor releases responsibility at `notify` (approval runs in another thread, F036), so the slot is never held across an approval.
3. `next_for_provider` → claim (T2: `status='processing'`, `attempts+1`, `claimed_at`, `stage_timings(dequeued)`).
4. Skip: `channel_id ∉ config.channels` → `done` + `skip_reason='channel_not_configured'`; `provider_id ∈ muted_authors` → `done` + `skip_reason='muted_author'`. (Stored and visible in feed; nothing silently dropped.)
5. Compute `age_s`, `stale`, `late`; call `processor.process(ctx)`.
6. Exception: if `attempts < poison_retry_limit` → set back to `received` with `last_error` (redacted) and retry **the same event** (order preserved); else `failed` + `PoisonHandler.on_poison` (alert `poison` now; raw review card added by F026) and continue with the next event.
7. Heartbeat task: upsert `dispatcher_heartbeat` every 5 s.
8. Startup: `UPDATE inbox SET status='received' WHERE status='processing'` then discover providers with `received` rows.

`StubProcessor` (this feature, tests + M2 demo): looks up a fixture interpretation for the message and marks done — replaced by F026's pipeline processor and then F035.

## Out of scope
Catch-up mode (F013), re-linking late events (F037), model semaphores (F031), multi-process advisory locks (documented upgrade only).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/dispatcher/service.py` | `Dispatcher` (workers, LISTEN, lifecycle) | interpretation |
| `backend/app/dispatcher/types.py` | `ProcessingContext`, `Processor`, `PoisonHandler` | — |
| `backend/app/dispatcher/classify.py` | pure: skip reason, staleness, age | I/O |
| `backend/app/dispatcher/stub.py` | `StubProcessor` for tests/demo | production logic |
| `backend/app/system/heartbeat.py` | heartbeat writer + reader | — |
| `backend/tests/unit/dispatcher/test_classify.py` | pure tests | — |
| `backend/tests/integration/dispatcher/test_sequencing.py` | ordering/parallelism/poison tests | — |

## Files to modify
`app/main.py` (start/stop dispatcher + heartbeat in lifespan), `app/config.py` (dispatcher enable flag for tests).

## Concurrency / idempotency
- One worker per provider; ordering by `(posted_at, message_id, event_type, event_seq)`.
- Multi-process upgrade (not built): session-level `pg_try_advisory_lock` per provider on a dedicated connection (schema doc §8).
- Re-processing after crash is safe because the processor's apply is idempotent (F026).

## Tests
- Unit: skip reasons; `stale` at `age = stale_entry_s ± 1 s`.
- Integration (StubProcessor with an artificial 200 ms delay):
  - AC-18 ordering: A1, B2, A3, B4 inserted in arrival order B2, A3, A1, B4 → A processed A1 then A3; B processed B2 then B4; A and B overlap in time.
  - Poison: processor raises for A2 three times → `failed`, alert raised, A3 still processed (AC-20 backend part).
  - Muted author/unconfigured channel → `done` with `skip_reason`, processor not called.
  - Restart: rows left in `processing` are reset and processed.
  - Heartbeat row updated at ≤ 5 s cadence.

## Acceptance criteria
- Per-provider serial processing in post-time order; cross-provider parallelism; poison never blocks a provider; no event skipped without a visible status.

## Implementation steps
1. Types + pure classify. 2. Worker loop with claim/mark. 3. LISTEN wake-up + idle exit. 4. Poison path. 5. Heartbeat. 6. Startup reset. 7. StubProcessor. 8. Tests.
