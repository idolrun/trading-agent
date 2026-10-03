# F078 — Interpretation outcomes, Jev time budget, safe failures, fallback by reason

## Objective
"No Jev answer" stops being one bucket. A timeout, an invalid answer, a semantic doubt, a doubt about which position, and a scope violation each lead to a defined place. No Jev problem can poison an event.

## Before (audit H1, H4, M3, M4, M6, scope)
- **Timeout:** `jev.timeout_ms = 300`, but measured p50 is 353 ms and p95 450 ms on 510 real calls (2026-10-03). 0 calls finished in time. The limiter wait sat inside that same timeout.
- **No reasons:** `JevInterpreter.interpret` returned `JevResult | None`, so disabled, timeout, 429 and invalid answer all looked the same.
- **Laya on everything:** every `slow_path` went to Laya, semantic doubt included. Laya's own evaluation: 27% type accuracy, uncalibrated confidence.
- **Crashes:** an answer outside the offered options, or a missing answer, raised `KeyError` → retry → poison. `ScopeViolation` escaped `jev_interpret` the same way.
- **Context:** models saw the whole lane book, not the resolver's candidates. Laya's `MAX_BOOK` counted the whole book. "Back in" priors never reached a model.

## Time budget (`JevConfig`, user decision 2026-10-03)

| Field | Default | Meaning |
|---|---:|---|
| `queue_timeout_ms` | 250 | wait for a `typesafe` limiter slot (own clock, `CallOutcome.QUEUE_TIMEOUT`) |
| `timeout_ms` | 700 | one request (≈ 1.5× the latest p95; also passed to the SDK call) |
| `budget_ms` | 1200 | the whole Jev step; must be ≥ queue + request (validator) |

- A 429/529 is retried once, only if its worst case (100 ms backoff + queue + request) still fits the budget.
- The Jev step adds at most about 1.2 s to a message.
- Migration `0027` writes 700 / 250 / 1200 into the stored config once, with an audit row. Downgrade strips the new keys.

## Outcomes
`JevInterpreter.interpret` returns `JevAnswered(result)` or `JevFailed(kind, detail, model_call_id)`.

| Kind | What happened | Next step | `interpret_reasons` |
|---|---|---|---|
| `disabled` | Jev off / no key | Laya → LLM → review | `jev: model_failure` |
| `timeout` | request over `timeout_ms` | Laya → LLM → review | `jev: model_failure` |
| `queue_timeout` | no limiter slot | Laya → LLM → review | `jev: model_failure` |
| `rate_limited` | 429/529 not retried | Laya → LLM → review | `jev: model_failure` |
| `api_error` | other API error | Laya → LLM → review | `jev: model_failure` |
| `connection` | network failure | Laya → LLM → review | `jev: model_failure` |
| `invalid` | the answers fail `validate_answers`: not offered, missing, wrong type, non-finite or out-of-range probability, score off the legend; or the mapper raised. Audited `schema_invalid`, answers kept, NaN stored as text | Laya → LLM → review | `jev: invalid` |
| `scope_violation` | user data in the state: nothing sent, audited `scope_blocked` | raw review `scope_violation` + urgent alert; **no model asked** | — |

When Jev answered but did not settle the message (`route()` gives `kind`):
- **`semantic`** (msg type below `t_type` / `t_chat`, several signals, a missing field, condition, correction): the optional LLM, else review. **Never Laya.** `interpret_reasons`: `jev: ambiguous`.
- **`link`** ("back in" with no contract and at least one earlier position in `reentry_candidates`): review directly. No model may pick a position (F077). `interpret_reasons`: `jev: ambiguous`.

Validation failures stay in `decide_apply`:
- a span mismatch is a raw review with outcome `validation_failure`;
- planner checks give review cards.
- Nothing is re-interpreted.

Each event's final outcome goes in `inbox.interpret_outcome` (`fast_success`, `jev_success`, `fallback_success`, `review`, `validation_failure`, `scope_violation`, `fixture`). `inbox.interpret_reasons` holds the per-interpreter trace, e.g.:

```
{"jev": "model_failure", "jev_detail": "timeout", "laya": "unsure", "llm": "not_configured",
 "route": "slow_path", "reasons": [...], "raw_reason": "..."}
```

Both are written in the same transaction as the cards (`apply_event`, `apply_raw_review`).

## Context the models see
- `_model_context()`: the resolver's `link_candidates` as `provider_book`, for Jev, Laya and the LLM.
- Laya's `MAX_BOOK` counts `link_candidates`.
- `ProviderContext.reentry_candidates`: the resolver's "back in" priors (today's closed positions and setups in the lane). They go into the Jev and Laya state as context, never as a link option.

## Files
- `app/interpret/jev/client.py`: outcomes, budget, validation, scope guard, NaN-safe audit.
- `app/interpret/jev/mapping.py`: `validate_answers`; `JevResult.review_reasons` (re-entry).
- `app/interpret/route.py`: `Kind`, `RouteDecision.kind`.
- `app/inbox/models.py`: `InterpretOutcome`, inbox columns.
- `app/graph/message_graph.py`: `_after_jev`, nodes, `interpret_outcome()`, `_scope_alert`, `_model_context`.
- `app/graph/state.py`: `fallback`, `trace`.
- `app/pipeline/apply.py`, `app/inbox/repo.py`: outcome persisted.
- `app/interpret/context.py`, `jev/state.py`, `laya/state.py`, `laya/client.py`, `ledger/resolver.py` (`Prior.summary`).
- `app/runtime_config/schema.py`, `app/llm/models.py`, `app/main.py`, `config.example.json`.
- Frontend config form: the two new fields.
- Migration `0027`.

## Tests
- `tests/integration/interpret/test_jev_client.py`:
  - timeout;
  - queue timeout (5 calls, 4 slots);
  - 429 not retried when the budget can't hold another request;
  - 5 invalid-answer shapes;
  - missing answer;
  - scope violation (nothing sent);
  - disabled.
- `tests/integration/graph/test_message_graph.py`:
  - timeout → Laya;
  - invalid answer → review on the first attempt, no failure;
  - Jev down without Laya → LLM;
  - scope violation → review + urgent alert, no other model, no model call;
  - unsure Jev → review without Laya / → LLM without Laya;
  - Jev down + unsure Laya → review with the trace;
  - outcome recorded on settled messages.
- `tests/integration/graph/test_paper_flow.py`: an unsure Jev is a review, not a Laya question.
- `tests/integration/interpret/test_laya_client.py`: a big book narrowed by the resolver still fits.
- `tests/unit/interpret/test_jev_mapping.py`:
  - semantic vs model-failure kinds;
  - "back in" with two priors → link review, priors in the state.
