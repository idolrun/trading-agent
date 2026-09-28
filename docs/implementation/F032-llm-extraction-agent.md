# F032 — LLM Extraction Agent (slow path)

## Objective
The LangChain `create_agent` slow path that turns a hard message (multi-signal, context-implied fields, missing candidates) into `ParsedSignals` using read-only tools, with guardrail middleware identical for every provider, running inside F031's fallback chain.

## Requirements covered
- PRD FR-6 (several signals per message), FR-7 (literal verification), FR-8 (no user portfolio), FR-9 (fallback → raw review card), FR-10 (uncertain → non-actionable), C31 (inferred fields highlighted with source), C71, C80 (LLM only interprets), AC-9, AC-11.
- TRD §4.6, §5.4 (context engineering, `@dynamic_prompt`, byte-stable prefix), §5.5 guardrail table, §5.6 tools (read-only), §4.8 rules 1, 3, 4, 7, 10.

## Dependencies
- Depends on: F031, F015, F017
- Produces: `LlmInterpreter` implementing F026's `Interpreter` (plus `hints` from Jev), prompt `extract@v1`.
- Used by: F035 (`llm_extract` node), F048 (replay, prelabel).

## Architecture
```
ProviderContext + Candidates + Jev hints
   → create_agent(model=registry.model("extract", attempt),
                  tools=[get_option_chain, get_quote, get_author_messages, get_provider_positions, get_message],
                  response_format=registry.response_format(LlmParsedSignals, provider),
                  middleware=[scope_guard, ToolCallLimitMiddleware(run_limit=2), extraction_prompt],
                  context_schema=ToolScope)            # {message_id, author_id} only
   → structured_response: LlmParsedSignals (raw strings, no offsets)
   → code: to_parsed_signals() + verify_literal_spans() + context-source check
   → ParsedSignals | ContentRejected
```
- `LlmParsedSignals`: same shape as F015 but literal fields carry only `raw` (the model cannot give reliable offsets); `context` fields must name a `source_message_id` from the provided context.
- **Middleware (identical across providers):**
  | Hook | Guardrail |
  |---|---|
  | `@before_model` + `@hook_config(can_jump_to=["end"])` | **Scope guard:** serialize the outgoing request; any `FORBIDDEN_KEYS` (F017) or a known lot ID → record `outcome='scope_blocked'`, jump to end, raise `ScopeViolation` (alert urgent — this is a bug, not a model failure; message → raw review card) |
  | `ToolCallLimitMiddleware(run_limit=2)` | ≤ 2 tool calls (TRD §5.5 [VERIFY] resolved: built-in exists) |
  | `@dynamic_prompt` | static system prompt + few-shot first, then volatile context (`now_et`, profile, C71 context, candidates, Jev hints) — byte-stable prefix for caching |
  | after the agent (in node code, so every provider is covered) | literal-span verification (F015) + context-source check; failure → `ContentRejected` (**no fallback**) |
- **Tools** read-only, scoped by `ToolRuntime.context` to the message author (`get_author_messages(n)` caps n at config window; `get_message(id)` only if id ∈ C71 set; `get_provider_positions()` = author's book summaries — never lots).
- Output with no confident signal → `ParsedSignals(signals=[…msg_type="chatter"…])`; the prompt instructs non-actionable when uncertain (FR-10).
- Prompt file versioned: `app/interpret/llm_extract/prompts/extract_v1.md` + few-shot examples built from synthetic messages only (never `docs/data/`, C87).

## Out of scope
Routing (F033), graph node wiring and timebox (F035), note (F034).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/llm_extract/agent.py` | `build_agent(model, provider)`, `LlmInterpreter.interpret()` running inside `run_chain` | portfolio access |
| `backend/app/interpret/llm_extract/schema.py` | `LlmParsedSignals`, `to_parsed_signals()` | — |
| `backend/app/interpret/llm_extract/tools.py` | five read-only tools | writes of any kind |
| `backend/app/interpret/llm_extract/middleware.py` | scope guard, dynamic prompt | trading rules |
| `backend/app/interpret/llm_extract/prompts/extract_v1.md` | prompt | real messages |
| `backend/tests/unit/interpret/llm_extract/test_middleware.py`, `test_schema.py`, `backend/tests/integration/interpret/test_llm_interpreter.py` | fake chat models (LangChain fake chat model with tool-call support — verify class name at implementation) | live API calls |
| `backend/tests/live/test_llm_providers.py` (marker `live`, skipped by default) | same 50 fixture messages per configured provider:model | — |

## Tests (TRD §12)
- Scope guard blocks a request containing `lot`/`balance` keys (every provider path).
- Literal span failure → `ContentRejected`, no second attempt.
- Multi-signal message → two `ParsedSignal`s; context-inferred expiry carries `source_message_id`.
- Tool call count > 2 → limited.
- Schema-invalid first model → fallback (via F031).

## Acceptance criteria
One output contract; guardrails provider-independent; AC-11 enforced on LLM output; FR-8 enforced at request time.

## Implementation steps
1. Schema + converter. 2. Tools with runtime scoping. 3. Middleware. 4. Prompt v1. 5. Agent builder + `LlmInterpreter` via `run_chain`. 6. Tests (fake models). 7. Live test harness (skipped).

## Open decisions touched
OD-7.
