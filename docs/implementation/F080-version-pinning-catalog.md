# F080 — Jev version pinning, question-set fingerprint, no unused questions, a replayable audit row

## Objective
A historical interpretation can be reproduced: message + context + candidate set + question catalog + model version. Every Jev question has a reader.

## Before (audit M1, M2, §28)
- `jev-latest` was used everywhere: schema default, `config.example.json`, migration 0017. An alias can move without any code change.
- `SystemOneResponse.model` (the model that actually answered) was discarded.
- The question-set version `jev_questions@v1` was edited by hand: wording or option changes did not move it.
- `correction_target` was asked on every call and never read.
- `is_add` was mapped into `fields.is_add`, which nothing downstream reads (the link decides add vs new).
- The audit row lacked the narrowed candidates, the resolver's verdict, the thresholds and the time budget.

## Model pinning
- `JevConfig.model` defaults to **`jev-1.13.0`**. Migration `0028` rewrites a stored `jev-latest` (or unset) value once, with an audit row; another explicit model is left alone.
- Checked against the API on 2026-10-03: `jev-latest` → `jev-1.13.0` and `jev-1.13.0` → `jev-1.13.0`. So the F076 corpus measurement and the F078/F079 numbers were taken on this version.
- New column `model_calls.model_returned` (response `model`). When it differs from what was requested, `jev:model_mismatch` is bumped and a warning is logged.
- An alias is still accepted, but only by explicit config (development).
- Replay and evalkit read the same config (`_Pinned(cfg)` keeps it), so production, evaluation and replay use one version.

## Question-set version
`questions.VERSION = "jev_questions@<sha256[:12]>"`, hashed over:
- the whole catalog (ids, primitive type, instructions, criteria), built over a fixed sample message so the per-message options (number roles, candidate picks, link and tier options) have a stable shape;
- `mapping.ROUTING_VERSION` (now 3), which is bumped by hand when the mapper's or the router's reading of the answers changes.

The version is stored on every Jev and Laya call (`prompt_version`; Laya appends `+laya_state@v1`). A golden test pins the current value, `jev_questions@d71b0158e813`, so any change is deliberate.

## Catalog
- `correction_target` and `is_add` are removed. The LLM schema keeps its own fields.
- `mapping.CONSUMED` names the reader of every remaining question; a unit test requires catalog = `CONSUMED`.
- See the F082 report for the full table.

## Audit row (`model_calls.request`)
Besides `questions` (version), `question_ids` and `state`, the request now holds:
- `model_requested`;
- `link_candidates` (the resolver-narrowed ids, which are the `links_to` options);
- `resolution` (outcome, position, candidates, method, evidence);
- `thresholds` (`t_chat`, `t_type`, `t_field`, `t_link`);
- `budget_ms` (queue / request / step).

The response holds every answer with its full probabilities. The row also has `model_returned`, `latency_ms`, `outcome`, `attempt` and the message key.

Together with `inbox.interpret_reasons` (F078) and `message_links.evidence` (F077), this answers "why did Jev associate this message with this signal?". Since F077 the answer is always "it did not; code did", with the Jev proposal recorded.

## Tests
- `tests/unit/interpret/test_jev_mapping.py`: every catalog question has a consumer; golden fingerprint.
- `tests/integration/interpret/test_jev_client.py`: the audit row holds the requested and returned model, fingerprint, candidates, resolver verdict, thresholds and budget; a model mismatch is counted.
- `tests/unit/runtime_config/test_schema.py`: pinned default.
- Live bench on `jev-1.13.0`: p50 377 ms, p95 915 ms (20 calls), msg_type 20/20.
