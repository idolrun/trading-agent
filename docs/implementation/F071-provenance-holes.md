# F071 — Provenance for every field

## Objective
For every interpreted value, say truthfully where it came from: the message text (`literal`), an earlier message (`context`), a code rule (`resolved`) or a model's judgment (`model`). Never label a model's answer as code.

## Before (audit)
- `exit_fraction`, `stop_basis`, `instrument`, the flags and `msg_type` were plain values with no provenance.
- A size tier that Jev read from the provider's lexicon was labelled `ResolvedSource("jev_size_tier")`, which claims code computed it.

## What changed
| Area | Change |
|---|---|
| `interpret/signal.py` | `ModelSource{kind: "model", interpreter, question, confidence}` joins the `Provenance` union. `ParsedSignal.field_sources: dict[str, Provenance]` covers the plain fields. |
| Jev / Laya (`jev/mapping.py`) | `msg_type`, `instrument`, `stop_basis` and the flags → `model` (with Jev's confidence). `exit_fraction` → `literal` when a `fraction_words` candidate says it, else `model`. The C52 trim→soft-exit override → `resolved` (`c52_soft_exit`). A size tier not in the text → `model` (fixes the false `resolved`). |
| Fast path (`interpret/fast.py`) | `msg_type` → `resolved` (`fast:<rule>`). A stated fraction → `literal`. The option entry's `instrument` → `literal` (the strike token). |
| LLM (`llm_extract/schema.py`) | Every plain field it sets → `model` (no confidence). |
| `spans.verify_literal_spans` | Also checks every `literal` entry in `field_sources` (exact span). |
| Persistence | `signals.field_sources jsonb NOT NULL DEFAULT '{}'` (migration `0023`), written in `apply._insert_signal`, read back by `stored_signal`. |
| Shared | `candidates.FRACTION_VALUES` / `fraction_value` (fast path and Jev mapping). |

`decide` is unchanged: only `context`-sourced values make an "inferred" card. A `model` size tier behaves as the old `resolved` one did.

## Tests
- `tests/unit/interpret/test_jev_mapping.py`: plain fields are `model`; a stated fraction is `literal`; an unstated one is `model`; a lexicon size tier is `model`; C52 is `resolved`.
- `tests/unit/interpret/test_fast_path.py`, `test_llm_schema.py`, `test_spans.py`: fast path, LLM and span verification of `field_sources`.
- `tests/integration/graph/test_fast_path.py`: stored and read back through `stored_signal`.
