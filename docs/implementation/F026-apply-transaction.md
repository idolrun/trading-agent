# F026 — `apply` Transaction (+ deterministic pipeline processor)

## Objective
Commit the outcome of one message event atomically: signals, links, provider-ledger changes, cards, timers, supersession and inbox status in **one** Postgres transaction — idempotent on re-run. Also wire a deterministic `PipelineProcessor` (fixture interpreter → validate → decide → apply) so the whole path runs before any model exists (Milestone M2).

## Requirements covered
- PRD FR-1a (apply in post-time order under the provider slot), FR-1c (stale entries update ledger, no card), FR-1e (poison → raw review card), FR-9 (raw review card with blank fields), §11 (reprocessing never creates a second card or order), G5, §14.
- TRD §2.1 `apply` ("ONE Postgres transaction: signals + message_links + provider ledger + card; card unique on (signal_id, action); inbox status = done"), §9 re-execution safety, schema doc T3.

## Dependencies
- Depends on: F019, F021, F024, F025 (card-expiry `timers` rows are inserted directly into the F005 table; the scheduler that fires them comes later)
- Produces: `apply_event(session, ApplyInput) -> ApplyOutcome`; `apply_raw_review(session, ctx, reason)`; `PipelineProcessor` implementing F012 `Processor`; `RawReviewPoisonHandler`; `Interpreter` Protocol + `FixtureInterpreter`.
- Used by: F029 (post-commit notify), F030, F035 (the `apply` node calls `apply_event`), F037, F038.

## Scope
```python
class Interpreter(Protocol):
    async def interpret(self, ctx: ProviderContext, candidates: Candidates) -> ParsedSignals   # Stub/Fixture now; Jev/LLM later
class ApplyInput(BaseModel): ctx: ProcessingContext; parsed: ParsedSignals
    per_signal: list[SignalOutcome]     # validation + link decision + proposed actions (computed BEFORE the tx)
class ApplyOutcome(BaseModel): card_ids: list[UUID]; superseded_card_ids: list[UUID]; feed_labels: list[str]; created: bool
```
**Before the transaction** (no locks held): load context, interpret, validate (market data I/O), build risk snapshot, run `decide`. **Inside T3:**
1. `lock_open_book(provider_id)` (`FOR UPDATE`) and re-resolve links against the locked book (book may have changed only by this provider's own earlier message — serial — so it is a consistency re-check; mismatch → raise and retry the event).
2. Insert `signals` (deterministic `signal_id`; `ON CONFLICT DO NOTHING`) — **if the first signal already exists, the event was applied before a crash: skip to step 7** (idempotent re-run).
3. Insert `message_links` rows.
4. Apply `PositionChange`s (F020) + `provider_position_events`.
5. Insert cards (`ON CONFLICT (dedupe_key) DO NOTHING`) + `card_events(created)` + `timers(card_expiry)` for pending cards.
6. Supersession (OD-14): invalidate pending entry/add cards on the same position (F025) → collect ids.
7. `inbox.mark(done | stale)`; `stage_timings(card)`; `NOTIFY cards`.
**After commit:** post-commit hooks (F029 notify; F036 start approval graphs / resume superseded ones).

- `apply_raw_review(ctx, reason)`: inserts `signals(msg_type='unparsed', interpreter='none')` + a `review` card (`action='review'`, all fields blank, the raw text shown) + alert; used by poison (FR-1e) and parser-degraded (FR-9) paths. `RawReviewPoisonHandler` replaces F012's alert-only handler.
- `FixtureInterpreter`: returns `ParsedSignals` from `tests/fixtures/interpretations/<message_id>.json` (dev/demo/tests only; never enabled in production settings).

## Out of scope
Models (F031–F034), LangGraph (F035), push (F029).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/pipeline/apply.py` | `apply_event`, `apply_raw_review` (T3) | model calls, broker calls |
| `backend/app/pipeline/processor.py` | `PipelineProcessor` (pre-tx steps + apply + post-commit hooks) | SQL beyond service calls |
| `backend/app/pipeline/hooks.py` | post-commit hook registry (list of async callables) | — |
| `backend/app/interpret/interpreter.py` | `Interpreter` Protocol, `FixtureInterpreter` | — |
| `backend/tests/fixtures/interpretations/*.json` | AC-18 and scenario interpretations | real data |
| `backend/tests/integration/pipeline/test_apply.py`, `test_pipeline_sequencing.py` | tests | — |

## Files to modify
`app/main.py` (dispatcher uses `PipelineProcessor` + `RawReviewPoisonHandler`), `app/dispatcher/stub.py` (kept for unit tests only).

## Concurrency / idempotency
- Serial per provider (slot) + `FOR UPDATE` on the book.
- Re-run after crash between commit and inbox mark is impossible (same tx); crash before commit → full rollback → re-process creates identical deterministic IDs.

## Tests (integration, real PG, fake market data)
- AC-18 end to end with `FixtureInterpreter`: A1/B2/A3/B4 → A3 linked to A's GOOGL, B4 to B's TSLA; cards correct.
- Re-running `apply_event` for the same event → no new rows (count assertions).
- Stale entry → ledger position created, no card, inbox `stale`.
- Ambiguous follow-up → review card with candidates, no ledger change.
- Poison 3× → raw review card + alert; next message processed (AC-20).
- Supersession: pending entry card invalidated when a stop for the same position is applied.

## Acceptance criteria
Milestone M2 path works with fixtures; no duplicate card on re-processing (TRD §9); every event ends in `done`, `stale` or `failed` with a visible record.

## Implementation steps
1. `Interpreter` Protocol + fixture interpreter. 2. `apply_event` steps 1–7. 3. `apply_raw_review` + poison handler. 4. Post-commit hook registry. 5. `PipelineProcessor`. 6. Tests.

## Open decisions touched
OD-14.

## Implementation notes (2026-09-29)
- Pre-tx planning lives in `pipeline/processor.py` (`PipelineProcessor._plan`): signals are planned in `idx` order over an in-memory copy of the provider book, so a later signal in the same message sees an earlier one's effect. `apply_event` locks the book and compares `{position_id: updated_at}` with the plan's snapshot; any difference raises `BookChanged` (the dispatcher retries the event).
- Deterministic ids: `signal_id = uuid5(NS, "signal:msg:evtype:seq:idx")`, new position `uuid5(NS, "position:<signal_id>")` (`ledger.repo.record(position_id=…)`), card `uuid5(dedupe_key "<signal_id>:<action>")`. Re-run = first signal exists → nothing but the inbox mark is written.
- The provider ledger changes only when validation is `ok` and the link is `linked`/`new_position`; `InvalidTransition` → one review card `ledger:<reason>`. A `new_position` link row is written only when the position was actually created (DB CHECK).
- Follow-up premiums still unscaled on a linked option position are scaled against that contract's live quote before the ledger/decide (`_scale_follow_up`); no quote → review `market_data_unavailable`.
- Inbox `stale` iff the context is stale **and** the event carried an entry/add; otherwise `done` (`late` copied).
- `NOTIFY cards` payload is JSON `{"type": "card.created"|"card.updated", "id": …}` (F029 bus contract). Card-expiry timers are scheduled with `timers.repo.schedule` (dedupe `card_expiry:<card_id>`); F027 adds the scheduler.
- `apply_raw_review` + `RawReviewPoisonHandler` (poison alert now carries `card_id`). `UnconfiguredInterpreter` (production until F035, user-confirmed) → raw review + `parser_degraded` alert; literal-span mismatch → raw review (AC-11). `FixtureInterpreter` via `INTERPRETATION_FIXTURES_DIR`, non-production only.
- Edit/delete events are marked `done` without cards until F038. `pipeline/hooks.py` not created: no post-commit consumer yet (push listens on the bus, F029; F036 adds approval-graph starts).
- Tests use their own full-contract messages (the AC-18 message fixture has no expiries): `tests/integration/pipeline/{harness.py,test_apply.py,test_pipeline_sequencing.py}`.
