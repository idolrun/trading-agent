# F048 — Evaluation Kit (labels, replay, gates)

## Objective
Measure interpretation quality before real money: LLM pre-labels, user-corrected labels split by trading day, a replay runner over stored messages using the real message graph (no side effects), gate metrics, and `eval_runs` whose IDs gate the LLM registry.

## Requirements covered
- PRD §17 (labeling via dashboard; dev/test split by trading day; each gate check logged; test set refreshed after repeated failures; replay gate: ≥ 500 actionable + 2,000 non-actionable from ≥ 5 providers, normal-card field accuracy 100%, zero wrong contracts on normal cards, actionable recall ≥ 98%), C64, C65 (history export prerequisite), C66, C67, AC-12 (stage 1), C87 (no personal trade data to models).
- TRD §12 replay harness (slow-path share, per-path latency, threshold sweep on dev only), §4.8 rule 5 (gate per `(provider, model, prompt)`), §13 phase 2.

## Dependencies
- Depends on: F035, F031
- Produces: CLI `app.evalkit` (`import`, `prelabel`, `replay`, `sweep`, `gate`), labeling API, `eval_runs` rows consumed by F031 config validation.
- Used by: F061 (labeling screen), F064 (stage gates).

## Scope
- **Import** (`evalkit import <file>`): historical Message API export → `message_events` with `backfilled=true`, **not** into `inbox` (never processed live). Format unknown (OD-1) → provisional adapter reused.
- **Split:** first label of a trading day assigns the day to `dev`/`test` deterministically (`hash(date) % 5 == 0 → test`); stored per label.
- **Prelabel** (`role='prelabel'`, batch, no deadline): LLM proposes `ParsedSignals` + link; stored as a pending label suggestion; the user confirms/corrects in F061 → `eval_labels` row.
- **Replay mode:** message graph with `apply` replaced by an in-memory collector; market data from recorded snapshots when available, else validation marked `skipped` (field accuracy still measured); context uses the ledger reconstructed by replaying labels in post-time order.
- **Metrics:** field accuracy on normal cards (exact match per field), wrong-contract count on normal cards, actionable recall/precision, per-type confusion, slow-path share, per-path latency p50/p95, cost.
- **Gate:** `evalkit gate --split test --model-ref provider:model:prompt` → checks PRD §17 stage-1 criteria incl. dataset size/provider count; writes `eval_runs(kind='replay_gate', gate_passed)`; logs each test-set check; ≥ 3 consecutive failures on the same test set → prints "refresh test set" (PRD C67).
- **Sweep:** thresholds `t_chat/t_field/t_link` over dev only.

## API contract (labeling)
| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/v1/labels/queue` | `?providerId&cursor` | `{items: [{eventKey, text, context, prelabel}], nextCursor}` | session |
| GET | `/api/v1/labels/{eventKey}` | — | `LabelDetail` | session |
| PUT | `/api/v1/labels/{eventKey}` | `{label: ParsedSignals+link, supersedes?}` | `LabelDetail` | session |

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/evalkit/cli.py`, `split.py`, `replay.py`, `metrics.py`, `gate.py`, `labels.py` | as named | live order paths |
| `backend/app/api/routes/labels.py`, `backend/app/api/schemas/labels.py` | labeling API | — |
| `backend/tests/unit/evalkit/test_metrics.py`, `backend/tests/integration/evalkit/test_replay.py` | tests with fixture corpus | real data |

## Security
Never reads `docs/data/` (C87); replay never calls the broker (assert).

## Tests
- Metrics on a hand-made corpus with known errors; wrong contract counted.
- Replay determinism (same inputs → same outputs with fake models).
- Gate fails on too-small dataset.

## Acceptance criteria
Stage-1 gate computable and recorded; registry validation (F031) consumes `gate_passed` runs.

## Implementation steps
1. Labels + split. 2. Import. 3. Prelabel. 4. Replay mode. 5. Metrics. 6. Gate + sweep. 7. API. 8. Tests.

## Open decisions touched
OD-1, OD-7, OD-8.
