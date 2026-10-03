# F081 — Interpretation and link metrics

## Objective
The health page answers four questions:
- how messages are interpreted (code, Jev, fallback, review);
- why Jev did not settle them;
- how follow-ups were linked;
- how often a code-made link turned out wrong.

## Sources (no new tables)
- `inbox.interpret_outcome` / `interpret_reasons` (F078).
- `message_links` (F077 evidence).
- `model_calls`.
- The in-process `jev:*` counters (F078/F080).

## `HealthDetails.interpretation` (`observability/queries.interpretation_stats`)

| Field | Meaning |
|---|---|
| `events` | interpreted events in the window |
| `outcomes` | counts per `interpret_outcome` |
| `path_rates.fast` | settled by code |
| `path_rates.jev` | Jev was asked |
| `path_rates.laya` | Laya was asked (answered, unsure, no answer); `off` excluded |
| `path_rates.llm` | the LLM was asked; `not_configured` excluded |
| `path_rates.review` | review, validation failure or scope violation |
| `path_rates.feed_only` | feed only |
| `jev` | Jev status counts: success / ambiguous / model_failure / invalid / scope_violation |
| `jev_failures` | model-failure detail: timeout / queue_timeout / rate_limited / api_error / connection / invalid / disabled |
| `feed_guard_blocks` | chatter verdicts refused by the F079 guard |
| `counters_since_start` | `jev:requests`, `jev:ok`, `jev:timeout`, `jev:queue_timeout`, `jev:rate_limited`, `jev:api_error`, `jev:invalid`, `jev:scope_violation`, `jev:ambiguous`, `jev:model_mismatch` |
| `links` | see below |

`links` covers follow-ups only (`evidence ? 'resolver'`), using the system's own decision (the first row per signal):

| Field | Meaning |
|---|---|
| `unique` | linked by code evidence |
| `ambiguous` | review |
| `model_proposed` | an acceptable model pick was recorded |
| `none` | nothing open in the lane |
| rates | `unique_rate`, `ambiguous_rate`, `model_proposed_rate` |
| `corrected_by_user` | a later `user_correction` row (re-link approval, manual link, ambiguous-card choice) moved a code-made link to **another** position |
| `wrong_link_rate` | `corrected_by_user` / `unique` |

`wrong_link_rate` is the live wrong-association signal. It only sees links a person corrected. The offline rate against labels is the evalkit replay (F082).

Other changes:
- `llm.models[*].latency_p99_ms`: Jev / Laya / LLM p50, p95, p99 from `model_calls`.
- Health screen: a p99 column, and an "Interpretation" table.

## Tests
- `tests/integration/observability/test_metrics.py`:
  - path shares, Jev statuses and failures, guard blocks and counters from seeded inbox traces;
  - link decisions, including a code link a person moved (wrong-link rate 0.5);
  - `details` carries the section.
- `frontend/features/health/health-screen.test.tsx`: the Interpretation table renders.
