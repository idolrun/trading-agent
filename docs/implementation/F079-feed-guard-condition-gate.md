# F079 — Feed-only guard and an explicit condition gate

## Objective
A model's "this is chatter" may not silence a message that could act on a position the provider holds. The `has_prose_condition` gate gets documented semantics.

## Before (audit H2, M7)
- **Feed-only gate:** `route()` made CHATTER / COMMENTARY_PNL / WATCH feed-only on msg-type confidence ≥ `t_chat` alone, with no code check. Jev read "gotta go here" (an exit) as chatter at 0.95 and "pop it here @everyone" at 0.83 (2026-09-30 replay).
- **Condition gate:** `has_prose_condition` sent a message to the slow path at p > 0.2, but `is_correction` only at ≥ 0.5. The asymmetry was not explained.

## Feed-only guard (`app/interpret/feed_guard.py`, pure)
`feed_block(text, ctx, candidates)` runs only when the lane book (live positions + today's setups) is non-empty. Pings (`@here`, `@everyone`, `<@id>`) are removed first. Any of these blocks feed-only:

| Evidence | Examples |
|---|---|
| action word | out, done, cut, stop/stopped, sl, trim, sold/sell, close, exit, take/taking/took, profit, runners, rest, half, BE (upper case only), b/e, breakeven, scratch, pop, gone, lock, pay yourself, paid, back in, re-entry, reload, add, average, flat, bail, dump, secure, tp, target |
| leaving now | "gotta go here", "get out now" |
| provider term | quoted terms in the provider's lexicon ("'yeet' means sell everything" → `yeet`) |
| fraction | %, half, third, quarter, most, rest, 1/2 |
| price | a decimal or `/con` number candidate |

`route(result, cfg, feed_block)` turns a blocked CHATTER / COMMENTARY / WATCH verdict into a **semantic** doubt, reason `feed_guard: <type> but <evidence> with a position open`. That goes to the optional LLM, else review.

The same guard applies to:
- Laya's feed verdicts;
- an LLM result that is all feed types (raw review `feed_guard`).

With no position open, nothing changes.

## Condition gate (`jev/mapping.py`, constants documented)
- **`conditional` / `watch` msg type:** a setup (F072). No gate.
- **Actionable type, `has_prose_condition ≥ 0.5`:** reason `conditional_instruction`.
- **Actionable type, `has_prose_condition` in `[1 − t_field, 0.5)`** (= `[0.2, 0.5)` by default): reason `condition_doubt`.
- Both reasons are semantic: the LLM, else review.
- Why the asymmetry: a missed condition turns "if it breaks 770, calls" into an unconditional order. A missed correction does not need the doubt band, because corrections also arrive as edits (F038) and the user sees the card they would change.

## Real Jev (`tests/live/test_jev_bench.py`, `jev-latest`, 2026-10-03)
Every probe went where it should:

| Message | Jev | Route (SPY position open) |
|---|---|---|
| "if SPY holds 770 then calls" | conditional @ 0.99, condition 0.92 | assemble (conditional setup) |
| "watching SPY around 770" | watch @ 0.96 | feed only |
| "SPY looks strong" | chatter @ 0.98 | feed only |
| "if this breaks I'm out" | chatter @ 0.69, condition 0.52 | slow path, guard on "out" |
| "gotta go here" | chatter @ 0.78 | slow path, guard on "leaving" |
| "pop it here @everyone" | chatter @ 0.65 | slow path, guard on "pop" |
| "Stopped last 3 …" | chatter @ 0.87 | slow path, guard on "Stopped" |
| "done" | chatter @ 0.68 | slow path, guard on "done" |
| "taking the rest" | full_exit @ 0.77 | slow path, below t_type |

Bench latency on the same run (20 calls): p50 384 ms, p95 998 ms (one slow outlier), msg_type 20/20.

## End-to-end scenario (spec §34)
`tests/integration/graph/test_multichannel_scenario.py`, two channels, three authors, ten messages.

Settled by code:
- the 5 entries;
- X "trim half" → SPY (X's only position in A);
- Y "stop breakeven" → META;
- X "out runners" in B → QQQ.

Asked Jev (2 calls):
- X "trim half" after X also opened META in A → review listing SPY and META; Jev's SPY pick stored as `proposal`; nothing changes.
- X "actually 580C" → correction → review.

No link leaves its lane.

## Tests
- `tests/unit/interpret/test_feed_guard.py`: 11 implicit instructions blocked; 5 chatter messages pass; no position → no block; pings are not words; lexicon term; blocked chatter → semantic doubt.
- `tests/unit/interpret/test_jev_mapping.py`: the four condition examples, plus the doubt band.
- `tests/integration/graph/test_message_graph.py`: confident chatter with a position open → review; the same answer with no position → feed-only.
- `tests/integration/graph/test_multichannel_scenario.py`: the scenario above.
- `tests/live/test_jev_bench.py`: the probes (asserts "never silenced" for the slow-path ones).

## Limits
The lexicon over-blocks: "check this out", "rest of the day", "take a look", any decimal. Each costs a review card while a position is open. The rate on the real corpus is measured in F082. Tuning needs the user's review of real false positives.
