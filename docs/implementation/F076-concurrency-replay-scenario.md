# F076 — Concurrency, single-instance guard, replay link metrics, end-to-end scenario

## 1. Account-level approval race (confirmed, fixed)
- **Traced:** `revalidate` builds a fresh risk snapshot whose working exposure counts **committed order rows** only. Two entry cards approved at the same moment each revalidate before either order row exists, so both pass on the same snapshot.
- **Confirmed** with `test_two_simultaneous_approvals_cannot_both_pass_the_exposure_limit`: 2 orders before the fix.
- **Fix:** `ApprovalService.execute` runs entries and adds under one account gate (`asyncio.Lock`). Inside it, the full preflight runs again against what is committed now, then `submit_order` commits the order row (T5a) before the gate opens for the next entry. A failure invalidates the card with the reason.
- Exits, stops and TPs are unchanged.
- After the fix: exactly 1 order; the other card is invalidated with "max exposure".

## 2. Single process (documented invariant, enforced)
- The TRD already says one process. Lane slots (F068), the entry gate and the bracket locks are in-process.
- `app/system/singleton.py`: at startup, with background services on, the app takes `pg_try_advisory_lock(LOCK_KEY)` on a dedicated connection held for its lifetime. A second instance raises `SecondInstance` and does not start. The lock is released automatically when the connection drops.
- F064 runbook: `--workers 1`, one replica.
- Multi-process remains **unsupported**: it is refused, not implemented.

## 3. Signal-state races
- Same-lane messages are serialized by the lane slot.
- Different lanes never share a book.
- `apply_event` re-locks the lane book (including setups) and compares versions (`BookChanged` → retry), which covers manual edits and relinks that touch the book concurrently.
- LangGraph threads stay per event (`msg:…:<attempt>`) and per card (`card:<id>`).

## 4. Replay metrics (`evalkit/metrics.py`, `evalkit/replay.py`)
- The label ledger is keyed by lane (F068).
- Each labeled follow-up that names a position is scored `correct` / `wrong` / `ambiguous`, after the code cross-check the replay can do: a pick contradicting the stated ticker counts as ambiguous, as live code refuses it.
- New metrics: `link_cases`, `links_correct`, `links_wrong`, `links_ambiguous`, **`wrong_link_rate`** (critical), `review_rate`, `path_shares` (fast / feed_only / assemble / slow_path), and `latency_ms_all` alongside the per-path p50/p95.
- The replay runs the same message graph and the same context builder as live (F073).
- Limit: the label ledger has no position states, so the replay's resolver only sees "every book position a candidate". Reply/thread/contract resolution is measured by the integration tests, not by replay.

## 5. End-to-end scenario
`tests/integration/graph/test_multichannel_scenario.py` runs an interleaved stream through the real graph:
- channel A: X SPY entry, Y META entry, X "trim half", Y "stop breakeven", X "out runners";
- channel B: X QQQ entry, Z TSLA entry.

It asserts:
- the 4 entries took the fast path and only the 3 follow-ups asked Jev;
- SPY open → trim → exit; META open → stop_move (breakeven); QQQ open; TSLA open;
- no stray position;
- every link inside its message's lane.

## Also fixed
- `jev/mapping.py`: a `links_to` id outside the offered candidates crashed with `StopIteration`. It is now `ambiguous` (unit test).

## Load suite (`tests/load -m load`, run alone on `trading_test`, 2026-10-03)
- **Kill tests:** the synthetic "Bought SPY 577c 10/2 @ 5.04" entries now take the fast path, so the Jev/LLM nodes the kill cases crash were never reached (timeouts). The model-path messages get " here" and pass again (15/15 message-graph cases).
- **Test 2 (50 messages / 5 s / 10 providers), queue wait p95 for the worst provider:**
  - Before (commit `9921fd3`, same machine): 2 of 3 runs pass, failing run 1.048 s (the PRD already records 1.034 s).
  - After: about 1.13–1.17 s in 3 of 3 runs → **FAIL**.
  - Overall latency improved in the same run: e2e p50 0.253 → 0.185 s, p95 1.251 → 1.095 s, inbox insert p99 38 → 20 ms.
  - The failing number is one lane waiting behind a simulated LLM call. The fakes draw every model latency from one shared seeded RNG, so 9 entries no longer calling fake Jev shifts every later draw.
  - The context/resolver reads were merged into one transaction anyway; no measurable change.
  - Open: give the fakes a per-message seed so code changes do not reshuffle simulated latencies, then re-baseline.
