# F035 — Message Graph (LangGraph)

## Objective
Replace the deterministic `PipelineProcessor` internals with the TRD §2.1 LangGraph `StateGraph` — one thread per message event — wiring context, candidates, Jev, routing, LLM slow path, validation, decision, apply and notify, with the provider slot held from `load_context` to `notify` and never across an approval.

## Requirements covered
- PRD §4 system shape, FR-1a (ordered; approvals never block), FR-1c, FR-1e, FR-9 (LLM timebox/failure → raw review card + "parser degraded"), FR-10, §12 latency (per-stage timing), AC-9, AC-18, AC-19, AC-20.
- TRD §2.1, §2.3 budget, §5.1 (graph + checkpointer), §5.2 (no chat history; context rebuilt), §5.3 (long-term store read), §5.7 (deterministic router), §9 re-execution safety. OD-11 resolved: this graph **ends at notify**; approvals run in F036.

## Dependencies
- Depends on: F012, F019, F026, F029, F032, F033, F034
- Produces: `build_message_graph()`, `GraphProcessor` (implements F012 `Processor`), checkpointer/store setup CLI.
- Used by: F036 (started from `notify`), F037, F038, F039, F048 (replay runs the same graph in replay mode), F049.

## Graph
```
START → load_context → find_candidates → jev_interpret → route ─┬─ feed_only ─────────────→ apply_feed_only → END
                                                                ├─ assemble ──→ assemble_signal ─┐
                                                                ├─ slow_path ─→ llm_extract ─────┤   (timebox; exhausted → raw_review)
                                                                └─ raw_review ─→ apply_raw_review → notify → END
                                              assemble_signal/llm_extract → validate → decide → apply → notify → END
```
- **State schema** (`TypedDict`, JSON-serializable): `event_key`, `provider_id`, `processing` (ProcessingContext dump), `context`, `candidates`, `jev`, `route`, `parsed`, `validations`, `decisions`, `apply_outcome`, `error`.
- **Nodes** are thin adapters over existing services (F017, F016, F033, F032, F019, F023/F024, F026, F029); no business logic in node bodies.
- **Conditional edges:** `route` → F033 `RouteDecision`; `llm_extract` → `validate` | `raw_review` (chain exhausted, deadline) | `review` (content rejected → validation marks review, card created as review).
- **Timebox:** `llm_extract` wrapped in `asyncio.timeout(config.llm.roles.extract.deadline_ms)`; exhaustion sets `parser_degraded` flag + alert (cleared on the next successful LLM call).
- **Checkpointer:** `AsyncPostgresSaver` on schema `langgraph` (connection with `options="-c search_path=langgraph"`), `thread_id = f"msg:{message_id}:{event_type}:{event_seq}:{attempt}"` (attempt makes a crash re-run start fresh from `load_context`, matching TRD §9). Durability mode for this short graph: `"exit"` (checkpoint once at the end, keeps per-node writes off the latency path — verify the `durability` parameter name at implementation).
- **Store:** `AsyncPostgresStore` namespace `("provider", author_id)` keys `lexicon`, `habits`, `aliases` read in `load_context` and passed into `ProviderContext.provider_profile`. Models never write the store (writes only F047).
- **Side effects:** only `apply*` (one DB tx) and `notify` (post-commit: SSE/push via F029, spawn `NoteWriter` task (F034), start approval graphs for new pending cards and resume superseded ones (F036 hook)). Model calls are side-effect free apart from `model_calls` audit rows.
- **Idempotency:** rerun of any node before `apply` is harmless; `apply` is idempotent (F026); `notify` re-emission is harmless (IDs only; push dedup by card id in-process).
- **Stage timings:** each node writes its `stage_timings` row (`context`, `jev`, `llm`, `validated`, `card`, `pushed`).
- **Termination:** every path ends in `END` with the inbox row `done`, `stale` or (via F012 poison path) `failed`.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/graph/state.py` | `MessageState` | logic |
| `backend/app/graph/message_graph.py` | nodes (adapters), edges, `build_message_graph()` | business rules |
| `backend/app/graph/checkpointer.py` | saver/store construction, `setup` CLI (`uv run python -m app.graph.checkpointer setup` as owner role), prune job hook | — |
| `backend/app/graph/processor.py` | `GraphProcessor` (invokes graph per event) | — |
| `backend/tests/graph/test_message_graph.py` | path tests with fake Jev/LLM/market data | live calls |

## Files to modify
`app/main.py` (processor = `GraphProcessor`, create saver/store in lifespan), `app/pipeline/processor.py` (kept as library functions used by nodes; class removed or retained for replay), `pyproject.toml` (`uv add langgraph-checkpoint-postgres`).

## Tests
- Each route path end to end (feed only, assemble, slow path, raw review on exhausted chain, content rejected → review card).
- AC-18 with fake Jev returning `links_to` → correct links; context isolation asserted from recorded `model_calls.request`.
- AC-19 stale entry / late exit through the graph.
- AC-20 poison via node exception thrice → raw review card, next message processed.
- AC-9: Jev down + LLM primary down → second vendor; all down → raw review + `parser_degraded`.
- Kill -9 simulation between nodes (cancel task) → re-run produces exactly one card.

## Acceptance criteria
Graph reproduces M2 behaviour with real interpreters; slot never held beyond `notify`; latency per stage recorded.

## Implementation steps
1. `uv add langgraph-checkpoint-postgres`; checkpointer/store setup CLI + migration note (owner role runs `setup()`). 2. State. 3. Nodes as adapters. 4. Edges. 5. `GraphProcessor`. 6. Tests. 7. Switch lifespan to graph processor.

## Implementation notes (2026-09-29)
- Files: `app/graph/{state.py,message_graph.py,checkpointer.py,processor.py}`; F026's per-signal planning moved to `app/pipeline/plan.py` (`Planner.plan`) and the `PipelineProcessor` class was removed (`pipeline/processor.py` keeps only `RawReviewPoisonHandler`). `UnconfiguredInterpreter` removed: production with Jev off and an empty `extract` chain now takes the real FR-9 path.
- Nodes: `load_context → find_candidates → (fixture_interpret | jev_interpret → route) → [llm_extract] → decide_apply → notify`, plus `raw_review`. **validate + decide + apply are one node (`decide_apply`)** so decided outcomes (proposals, ledger updates) never pass through the checkpoint; it re-reads the provider book (serial per provider, and `apply_event` re-checks it under the lock). `route` runs inside `jev_interpret` (pure F033 function) and picks the edge.
- `fixture_interpret` replaces Jev + LLM only when a `FixtureInterpreter` is injected (non-production + `INTERPRETATION_FIXTURES_DIR`); the M2 demo and F026 tests run through the graph this way.
- `llm_extract`: `asyncio.timeout(llm.roles.extract.deadline_ms)` around `LlmInterpreter.extract`. `exhausted`/timeout → raw review `parser_degraded` + `parser_degraded` flag (its `system_flag` alert is the FR-9 alert); any later `ok` clears the flag. `content_rejected` → raw review `llm_content_rejected:<reason>` (no fallback; scope blocks already alert in F032). Literal-span check runs in `decide_apply` for every interpreter (AC-11).
- `signals.model_call_id` = the Jev call or the answering LLM attempt (`ApplyInput.model_call_id`). Per-stage rows: `context`, `jev`, `llm`, `validated`, `card` (apply), `pushed` (F029) via public `apply.record_stage`.
- `notify` spawns `NoteWriter.write` for new pending, non-review cards as tracked background tasks (cancelled at shutdown); SSE/push come from the apply transaction's NOTIFY. F036 adds approval-graph starts here.
- Checkpointer/store: `AsyncPostgresSaver`/`AsyncPostgresStore` on a psycopg pool with `search_path=langgraph`; tables created by `uv run python -m app.graph.checkpointer setup` (owner role; tests run it once per session and truncate the data tables per test). Migration 0001's downgrade now drops the schema `CASCADE` (LangGraph's tables live there). `thread_id = msg:<id>:<type>:<seq>:<attempt>`; `durability="exit"` writes one checkpoint per run — also for a cancelled run, recording where it stopped. Every run starts from a full `initial_state`, so a reused thread never leaks values. Checkpoint prune job: F049.
- Provider profile store contract: namespace `("provider", <author_id>)`, keys `lexicon`/`habits`/`aliases`, value `{"items": [str, …]}` (written by F047 only).
- Tests: `tests/integration/graph/test_message_graph.py` (feed-only, assemble, slow path, exhausted + flag cleared, content rejected without fallback, AC-18 request isolation, notes spawned, crash mid-LLM → one card). AC-19/AC-20 run through the graph via the F026 pipeline tests.
