# F070 — Deterministic fast path

## Objective
An obvious message must not wait on a model, and must not depend on one being up.

## What changed
| Area | Change |
|---|---|
| `app/interpret/fast.py` (new, pure) | `fast_parse(text, resolution, tiers) -> ParsedSignals | None`. Whole-message rules: option entry (optional `bto/bought/buy/entry/in`, expiry before or after the strike, `@`, `sl`, `tp a/b`, a configured tier word), full exit, trim with a fraction, breakeven stop. Follow-ups need `resolution.outcome == "unique"` and point at that position. A trim verb `sold/took` needs a stated fraction; a bare `sold` goes to Jev. Every value is a literal span. |
| Message graph | `fast_interpret` after `find_candidates` (non-fixture path). A hit goes to `decide_apply` (or END in recheck/replay). A miss goes to `jev_interpret`. Create events only. |
| Contract | `ParsedSignals.interpreter` / `Interpreter` gain `fast`; `Planner` maps it to `LinkMethod.CODE` (entries link by contract, follow-ups by the resolver). |
| Migration `0022` | `signals.interpreter` and `stage_timings.stage` gain `fast`. |

## Not on the fast path (Jev decides)
Conditions ("if SPY holds 770 then calls"), watch, "out most", "out 2" (count or price?), "sold" alone, re-entry phrases (F072), any message with an unexplained word, multi-line messages, an ambiguous follow-up.

## Tests
- `tests/unit/interpret/test_fast_path.py` (15): entries with every optional part; rejected shapes; follow-ups need a unique resolution; follow-up rules. Every hit passes `verify_literal_spans`.
- `tests/integration/graph/test_fast_path.py`:
  - entry + replied trim make zero Jev calls, interpreter `fast`, the trim linked `reply`, stage `fast` timed;
  - an unresolved trim calls Jev;
  - a conditional calls Jev.
- `tests/integration/pipeline/test_apply.py`: with every model off, an obvious entry is still a normal card.
- Tests that exercise Jev/Laya now use a non-obvious text ("… here") so they still do.

## Limits
- An expiry-less entry ("SPY 765P .54") is parsed but validation still makes it a review (`contract_incomplete`): no default expiry is ever assumed.
- No stock-entry rule (stock entries need sizing by % of equity, C76, still open).
