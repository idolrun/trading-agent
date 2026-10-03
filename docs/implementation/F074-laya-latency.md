# F074 — Laya latency containment and observability

## Objective
A slow local fallback must never stretch an unrelated lane's queue, and every interpreter must be measurable on its own.

## Before (audit)
- Laya takes 3–13 s through one GPU slot shared by every author.
- A second message waited for the slot inside its own `timeout_ms` (10 s) while holding its provider slot, so one author's channels all stalled behind it.
- Laya was timed as stage `jev`.

## What changed
| Area | Change |
|---|---|
| Lanes (F068) | A Laya call holds only its own lane's slot. The same author's other channels and every other author keep flowing. |
| `interpret/laya/client.py` | If the slot is taken, skip at once: `CallOutcome.BUSY` is audited in `model_calls` and the message continues to the optional LLM or a review card. Nothing waits. The explicit `timeout_ms` stays. A timed-out call is abandoned: its thread finishes and frees the slot (existing shield), so two calls never run on the model. |
| Message graph | `laya_interpret` records stage `laya` whenever Laya was asked, whether it answered, timed out or was busy. |
| Migration `0026` | `model_calls.outcome` + `busy`; `stage_timings.stage` + `laya`. |

Ordering is unchanged: inside a lane, messages are still processed one at a time in post order.

## Tests
- `tests/integration/interpret/test_laya_client.py`:
  - a second concurrent lane returns in < 0.1 s while the first holds the model for 0.3 s; outcomes OK + BUSY;
  - the timeout test now records TIMEOUT, BUSY, TIMEOUT.
- `tests/integration/graph/test_message_graph.py`: stage `laya` is recorded.

## Latency (to measure, not claimed)
Per-path p50/p95 come from `stage_timings` (`fast`, `jev`, `laya`, `llm`) and the evalkit replay `latency_ms` by path (F076).
