# F033 — Jev Fast Path & Routing

## Objective
The primary interpreter: one TypeSafe Jev request per message answering the Appendix A questions over code-found candidates, mapped to `ParsedSignals` with literal provenance, and the code routing rules that decide feed-only / assemble / slow path / review.

## Requirements covered
- PRD FR-5 (classification types), FR-7, FR-10 (uncertain → non-actionable), C31 (self-reported confidence never trusted to upgrade), C78 (no invented numbers), §10.
- TRD §1.2–1.3, §3 (`typesafe-sdk`, `AsyncTypeSafeClient`, pinned model), §4.1–4.5, Appendix A (question catalog + A.3 mapping rules), §2.4 (`N_jev` semaphore), §7.4 (429/529 retried once, then slow path), §13 phase 0 (bench go/no-go, OD-8).

## Dependencies
- Depends on: F016, F017, F031 (`record_model_call`, limiter)
- Produces: `JevInterpreter`, `build_jev_state(ctx, candidates)`, `build_questions(state, config)`, `map_answers(...) -> JevResult`, `route(result, cfg) -> RouteDecision`.
- Used by: F035 (`jev_interpret` + `route` nodes), F048 (bench/replay).

## Scope
- **State** = TRD §4.2 JSON built from `ProviderContext` + `Candidates` (no user portfolio; same forbidden-key check).
- **Questions** (Appendix A): A.1 `msg_type`, `signal_count`, `instrument`, `role_<nX>` per number candidate, `ticker` (options = ticker candidates + `not_stated`), `right`, `expiry`, `size_tier` (config tier names + `not_stated`), `exit_fraction`, `stop_basis`, `candle_close`, `has_prose_condition`, `is_correction`, `correction_target`; A.2 `links_to` (book ids + `new_position` + `ambiguous`), `is_add`, `exit_intent` (Score 0–4), `breakeven_stop`, `stop_removed`. Question wording lives in `jev/questions.py` as data (tunable, versioned `jev_questions@v1`).
- **Call:** `AsyncTypeSafeClient.system_one(state=…, questions=…)`, timeout `config.jev.timeout_ms`, inside the `N_jev` semaphore; `TypeSafeAPIError` 429/529 → one retry with backoff, then route to slow path. Record `model_calls(provider='typesafe', role='interpret')` with every question's probabilities (exact response field names verified in S4 — TypeSafe docs page does not show them).
- **Mapping (A.3):** a field is literal only when Jev picks a candidate with confidence ≥ `t_field`; value = candidate's exact span (`Literal_` with offsets + `candidate_id`); two numbers claiming the same single-valued role → slow path; `not_stated` on a required field → slow path.
- **Routing (TRD §4.4, code):**
  | Condition | `RouteDecision` |
  |---|---|
  | `msg_type ∈ {chatter, commentary_pnl, watch}` with confidence ≥ `t_chat` | `feed_only` |
  | required field `none`/`not_stated` or confidence < `t_field` | `slow_path` (Jev answers passed as hints) |
  | `signal_count = multiple` | `slow_path` |
  | `links_to = ambiguous` or confidence < `t_link` | keep going, but link hint = ambiguous → review card (F021); no model upgrades |
  | all confident + complete | `assemble` |
  | Jev disabled/unavailable | `slow_path` |
- `exit_intent` Score 2 with type `soft_exit`/`trim` ambiguity → `soft_exit` label (C52) — mapping rule documented in code with tests.

## Out of scope
Threshold tuning (F048), graph wiring (F035).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/interpret/jev/state.py` | state builder | portfolio |
| `backend/app/interpret/jev/questions.py` | Appendix A as data | — |
| `backend/app/interpret/jev/client.py` | SDK wrapper (timeout, retry, semaphore, `model_calls`) | mapping |
| `backend/app/interpret/jev/mapping.py` | answers → `ParsedSignals` | routing |
| `backend/app/interpret/route.py` | `route()` pure | I/O |
| `backend/tests/unit/interpret/jev/test_mapping.py`, `test_route.py`, `test_questions.py` | recorded/fake Jev responses | live calls |
| `backend/tests/live/test_jev_bench.py` (marker `live`) | S4 bench: latency p50/p95, accuracy on 100 labeled synthetic/export messages | — |

## Files to modify
`pyproject.toml` (`uv add typesafe-sdk`), `app/config.py` (`typesafe_api_key: SecretStr`).

## Tests
- TRD §4.2 example → strike 770 (n1), entry .95 (n2), stop 769 underlying (n3), ticker SPY, tier lotto → `assemble`.
- Two `strike` roles → slow path; `links_to` 0.6 → ambiguous hint; chatter 0.95 → feed_only; 429 twice → slow path.
- Literal provenance always references a candidate id; values equal spans.

## Acceptance criteria
Fast path produces verified literal fields only; routing matches TRD §4.4 table; Jev failures degrade to slow path without loss.

## Implementation steps
1. State builder. 2. Question catalog. 3. Client wrapper. 4. Mapping. 5. Route. 6. Tests. 7. Bench harness.

## Open decisions touched
OD-8.
