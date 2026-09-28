# F063 — Load & Fault Test Suite

## Objective
Prove latency and no-loss guarantees under the PRD design bursts and failure modes before the shadow stage.

## Requirements covered
- Decision log: C43.
- PRD §12 (p95 ≤ 5 s, p99 ≤ 8 s incl. bursts; ≥ 50 msgs/10 s; ≥ 5 msgs/5 s single provider), §17 stage 2 prerequisites (real day at 1×/10×/50×, bursts, API outage + catch-up, kill during processing, DB failover), AC-7, AC-21, AC-22, AC-23, §21 (single-provider slow-path risk).
- TRD §12 load and fault tests 1–6 and pass criteria; §2.3 upgrade path (OD-19) if only test 3 fails.

## Dependencies
- Depends on: F046, F049
- Produces: `backend/tests/load/` harness + report (markdown written to `docs/reviews/load-<date>.md` by the runner).

## Tests (TRD §12)
| # | Test | Pass |
|---|---|---|
| 1 | One full day replayed at 1×, 10×, 50× (real export when available, synthetic until then) | p95 ≤ 5 s, p99 ≤ 8 s end to end |
| 2 | 50 messages in 5 s across 10 providers | same + per-provider queue wait p95 ≤ 1 s |
| 3 | 5 messages in 3 s from one provider, slow path forced | same; if only this fails → OD-19 (parallel interpret, serial apply). **Not** Kafka/Redis |
| 4 | 10-minute Message API outage then backlog delivery | zero lost/duplicated; catch-up shown then cleared; stale entries uncarded, exits carded "late" |
| 5 | kill -9 in each graph node (message + approval graphs), restart | no duplicate card or order |
| 6 | Postgres failover during a burst (local: stop/start container; staging: managed failover) | no lost accepted event; RTO measured ≤ 5 min; reconcile before entries |
Also: inbox insert p99 < 50 ms.

## Scope
Model latency simulated by fakes with recorded latency distributions (from F048 bench) so results reflect realistic slow-path share; one run with real models in staging before shadow.

## Files to create
`backend/tests/load/{driver.py,scenarios.py,report.py}`, `backend/tests/load/README.md` (how to run), synthetic day generator.

## Acceptance criteria
All six tests meet pass criteria (or OD-19 applied and re-run); report committed.

## Implementation steps
1. Driver + report. 2. Tests 1–3. 3. Outage test. 4. Kill tests. 5. Failover test. 6. Analyse; apply OD-19 only if needed.

## Open decisions touched
OD-19.
