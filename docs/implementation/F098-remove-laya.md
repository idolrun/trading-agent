# F098 — Remove Laya

## Objective
Remove the local Laya fallback (`laya-mlx`) completely. Interpretation is now fast path -> Jev -> optional LLM -> review. F088 had already turned Laya off by default: it named the right message type 27% of the time, took 3–10 s on one GPU slot, and held the lane while it ran.

## Decision (2026-10-13)
- A Jev model failure (off, timeout, API error, invalid answer) goes straight to `llm_extract`, like a semantic doubt. Without an LLM it becomes a review card. With Laya off this was already the live behaviour.
- Removed: `app/interpret/laya/`, the `laya_interpret` graph node and `GraphDeps.laya`, `RuntimeConfig.laya` (`LayaConfig`), the `LAYA_MODEL` setting, the `laya-mlx` dependency (and its mlx packages in `uv.lock`), the Config page section, and the "Asked Laya" health metric (`path_rates.laya`). The now-unused `fallback` graph state key went with it.
- Kept, for stored rows only: the `laya` enum values (`model_calls.provider`, `signals.interpreter`, `message_links.method`, `stage_timings.stage`), the `busy` call outcome, their CHECK constraints, and `"laya"` in the `ModelSource` / `ParsedSignals` interpreter literals. No migration; the old migrations are untouched.
- Stored config documents and `config_changes` history that carry a `laya` key still load: `laya` joined `RETIRED_KEYS` (dropped on read).

## Tests
- Laya unit, integration and live tests deleted.
- `tests/integration/graph/test_message_graph.py`: a Jev timeout or outage goes to the LLM; a Jev outage without an LLM is a review card (`needs_review: jev_rate_limited`).
- `tests/integration/graph/test_paper_flow.py`: a Jev outage without an LLM gives a review card and no order.
- `tests/unit/runtime_config/test_schema.py`: a document with a `laya` key still loads.
