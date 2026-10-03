# Jev hardening (F077–F082): real-corpus measurement, 2026-10-03

**Provisional.** The labels were written by Claude (`docs/data/labels-assistant-2026-10.json`, local and gitignored like the corpus; source `assistant`), with message types for rows 1–130 taken from the 2026-09-30 hand-made key. 26 judgment calls are listed under `spot_check`. The numbers stand once the user has reviewed them.

## Setup
- **Corpus:** 519 real Discord message events from `trading_replay`, copied to the scratch DB `trading_measure`. 2 channels, 14 authors, 2026-09-24 to 2026-09-30.
- **Labels:** 185 actionable messages, 112 follow-ups linked to the message that opened their position, 84 replies, no threads.
- **How it ran:** the real message graph in replay mode (interpretation only: no ledger writes, cards or broker), over both splits (289 dev + 230 test).
- **Models:** real Jev `jev-1.13.0`; Laya off; no LLM configured (none is configured in this app).
- **Before:** the pre-hardening code at `b0be248` (F076) on the same labels and DB. Jev timeout 10 s, as in the F076 measurement; with its shipped 300 ms every call timed out. Old replay rules: no resolver, ticker-only link check.
- **After:** HEAD with F077–F082. Jev 700 ms request / 250 ms queue / 1200 ms budget. The replay runs the production resolver and `resolve_link` over the label history.

## Safety metrics

| Metric | Before | After |
|---|---:|---:|
| **Wrong links** (code linked a follow-up to the wrong position) | 0 / 112 | **0 / 112** |
| **False feed-only** (labeled instruction silenced) | 2 / 185 (1.1%) | **0 / 185** |
| Follow-ups linked correctly | 0 | 9 (8 in a second run) |
| Follow-ups left to review (ambiguous / not recognized) | 112 | 103 |
| Wrong contracts on settled cards | n/a (metric changed) | 1 (see below) |
| Actionable precision | 1.0 | 1.0 |
| Actionable recall (settled correctly without a person) | 9.1% | 16.1% |

- **The 2 silenced instructions (before):** "taking 35% loss in those U leftover" and "unfortunately MSFT … sold off during final hour, hit SL". Jev called both commentary at 0.98 and 0.91. After, the F079 guard sends them to review.
- **The 1 wrong contract (after):** "all out HAGW @everyone". The fast-path exit rule read "HAGW" (have a good weekend) as a ticker. The follow-up still went to review, because the correlation cross-check found HAGW contradicts the replied-to IWM position. It fails safe, but the rule should not treat a trailing all-caps word as a ticker (open item).
- **Wrong links are 0 before and after, but this corpus barely tests them.** Before, Jev never produced an assemblable follow-up (msg-type confidence too low), so no hint was ever accepted. The "two positions, no evidence" case F077 fixes is covered by tests (`test_correlation.py`, `test_multichannel_scenario.py`), not by this corpus.

## Path shares (519 messages)

| Path | Before | After |
|---|---:|---:|
| Settled by code (fast path) | 9 (1.7%) | 18 (3.5%) |
| Assembled by Jev | 9 (1.7%) | 13 (2.5%) |
| Feed only | 126 (24.3%) | 111 (21.4%) |
| Review (a person decides) | 376 (72.4%) | 377 (73.0%) |

The fast path doubled. The new settled messages are sole-position follow-ups such as "trim 1.52", "out runners", "2.05 trim".

### Why messages end in review (after, from the audited Jev answers)

| | Actionable (155) | Non-actionable (222) |
|---|---:|---:|
| Jev unsure of the type, a field missing, or a condition | 122 | 59 |
| Feed type below `t_chat` (0.9) | 21 | 131 |
| Feed guard (feed ≥ 0.9, position open, action evidence) | 2 | 17 |
| Jev timeout (700 ms) | 10 | 15 |

- **Review rate is still ~73%.** The cause is unchanged since 2026-09-30: Jev's msg-type confidence on this provider language, with no LLM fallback configured.
- **The guard is not the driver.** It costs 17 extra reviews (5% of non-actionable messages) and caught the 2 silenced exits.
- **Lowering review volume needs a second LLM vendor** (the slow path) or threshold work on a person-checked corpus. It does not need a looser guard.

## Latency (`jev-1.13.0`)

| | Run 1 | Run 2 |
|---|---:|---:|
| Jev calls (all answered by `jev-1.13.0`) | 501 | 501 |
| ok / timeout at 700 ms | 476 / 25 (5.0%) | 496 / 5 (1.0%) |
| ok-call p50 / p95 / p99 | 368 / 487 / 616 ms | 375 / 498 / 574 ms |
| Message p50 / p95 (graph, replay) | 394 / 710 ms | — |

Before (10 s timeout): message p50 380 ms, p95 510 ms.

- **The new budget caps the tail.** A slow call is a timeout at 700 ms, not a 7.8 s wait. That costs 1–5% of messages as model failures (Laya, then review).
- **The timeout rate varied 5× between two runs an hour apart.** Watch `jev_failures.timeout` on the health page before moving the timeout.

## Open
- The user spot-checks the labels (`spot_check` list), then re-run: `uv run python -m app.evalkit replay --split dev|test`, against a DB with the labels imported.
- Fast-path follow-up rules: do not take a trailing all-caps word as a ticker ("HAGW").
- Review rate: configure the LLM slow path, then re-measure.
- Wrong-link rate on real data needs follow-ups that Jev settles. Revisit after the slow path is configured, and watch the live `wrong_link_rate` on the health page (F081).
