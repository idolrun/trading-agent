# F031 — LLM Provider Layer (registry, fallback chain, cost)

## Objective
A provider-agnostic LLM layer: config-driven model registry per role, capability table, per-provider concurrency + circuit breaker, a fallback chain under one shared deadline, normalized usage/cost, and a `model_calls` row for every attempt. No provider-specific code exists outside `app/llm/`.

## Requirements covered
- PRD FR-9 (LLM failure → second provider → raw review card + "parser degraded"), C36 (cloud LLMs allowed), C45 (second provider; both pass the same gates), C44 + §19 (spend tracked from day one), §14 (LLM calls: input context, output, provider, latency, cost), §16 (fallback rates, spend), AC-9.
- TRD §4.8 rules 1–10, §2.4 (one semaphore per provider; 429 wait counted as queue wait), §3 (`init_chat_model("<provider>:<model>")`), §7.4, §12 LLM provider tests.

## Dependencies
- Depends on: F003, F006, F008
- Produces: `LlmRegistry`, `CapabilityTable`, `run_chain(role, attempt_fn, *, deadline_ms) -> ChainResult`, `CircuitBreaker`, `ProviderLimiter`, `record_model_call(...)`, config validator hook `validate_llm_config(cfg)`.
- Used by: F032, F033 (records Jev calls with the same writer), F034, F048.

## Scope
- **Registry schema** (inside `RuntimeConfig.llm`, F006): `roles.{extract,note,prelabel}.{deadline_ms, per_attempt_cap_ms, chain:[{provider, model, prompt, gate_run}]}`, `prices["provider:model"] = {input_per_mtok, output_per_mtok, cached_input_per_mtok}`, `params["provider:model"] = {...}` (pass-through), `concurrency[provider] = int`.
- **Capability table** (`llm/capabilities.py`, code constant; every cell re-verified in S5 bench): structured output strategy (`anthropic`/`openai` → `ProviderStrategy`; `deepseek` → `ToolStrategy(handle_errors=False)` — [VERIFY]), tool calling, caching mode, whether `temperature` may be sent.
- **Model construction:** `init_chat_model(f"{provider}:{model}", **params)`; OpenAI-compatible endpoint via `ChatOpenAI(base_url=…)`; Bedrock optional (not built until chosen, OD-7).
- **Chain execution** (`run_chain`): attempts in order; each gets `min(remaining_deadline, per_attempt_cap)`; skip providers whose breaker is open (`outcome='circuit_open'` recorded); acquire the provider semaphore (wait time returned for queue-wait metrics). Fallback on `timeout, rate_limited, server_error, connection_error, schema_invalid, empty, refusal`. **No fallback** on `span_rejected` or content validation failures ("no answer shopping", rule 3) — return `ChainResult(status="content_rejected")`. Exhausted/deadline → `ChainResult(status="exhausted")` → caller creates a raw review card + sets `parser_degraded` flag + alert (FR-9).
- **One model per message** (rule 4): results are never merged across attempts.
- **Circuit breaker** per provider: 5 consecutive failures within 60 s → open 30 s → half-open single probe.
- **Missing API key** (`Settings.anthropic_api_key`, `openai_api_key`, `deepseek_api_key` as `SecretStr`) → provider disabled at startup + warning alert.
- **Config validation** (registered into F006 `update()`): every `extract`/`note` chain entry references an `eval_runs` row with `gate_passed=true` and matching `model_ref`; `extract` chain has ≥ 2 distinct vendors (Bedrock-hosted Claude ≠ second vendor); in `environment=development` an explicit `allow_ungated_models=true` setting bypasses (never in production).
- **Usage/cost:** from `AIMessage.usage_metadata` (input, output, cache read) × registry prices → `cost_usd`; written per attempt to `model_calls` in its own short transaction (never inside `apply`).

## Out of scope
Prompts and the agent (F032), Jev client (F033), note (F034), bench tooling (F048).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/llm/registry.py` | registry model + lookup + model factory | prompts |
| `backend/app/llm/capabilities.py` | capability table + `response_format_for(schema, provider)` | — |
| `backend/app/llm/chain.py` | `run_chain`, `ChainResult`, error classification | business rules |
| `backend/app/llm/breaker.py`, `backend/app/llm/limiter.py` | breaker, per-provider semaphores | — |
| `backend/app/llm/usage.py` | cost computation, `record_model_call` | — |
| `backend/app/llm/validation.py` | config validator | — |
| `backend/tests/unit/llm/test_chain.py`, `test_breaker.py`, `test_usage.py`, `test_validation.py` | fake chat models, fake clock | — |

## Files to modify
`pyproject.toml` (`uv add langchain-deepseek`; confirm `langchain-anthropic`, `langchain-openai`), `app/config.py` (API keys), `app/runtime_config/schema.py` (typed `llm` section), `app/main.py` (startup key check).

## Security
API keys only in env (`SecretStr`), redacted from logs; request bodies stored in `model_calls` are C71-scoped (no user portfolio — enforced upstream by F017 types and F032 scope guard).

## Tests (TRD §12 LLM provider table)
- Primary timeout → second vendor answers within shared deadline; `model_calls` attempts 1 and 2.
- Primary schema-invalid → fallback.
- Content rejection → **no** fallback.
- All fail / deadline passes → `exhausted`.
- 5 failures in 60 s → circuit opens; skipped 30 s; half-open probe.
- Live chain entry without passing `gate_run` → config rejected; single-vendor extract chain → rejected.
- Cost computed from usage metadata and price table (Decimal).

## Acceptance criteria
AC-9 building blocks; every attempt recorded with outcome, latency, tokens, cost.

## Implementation steps
1. Registry schema in F006 config. 2. Capability table. 3. Model factory. 4. Limiter + breaker. 5. `run_chain` + error classification. 6. Usage/cost + `record_model_call`. 7. Config validator. 8. Tests.

## Open decisions touched
OD-7, PRD (d).
