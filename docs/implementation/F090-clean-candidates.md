# F090 — Clean candidates before Jev

## Objective
Implements ranked fix #2 from `docs/reviews/signal-bottleneck-2026-10-12.md` §4.4. A single message could produce up to 65 number candidates, and each one adds a `role_nN` question that must clear `t_field`. Ticker candidates were mostly ordinary words.

## Decisions (2026-10-13, after a replay)
- **New function, old one unchanged.** `clean_candidates()` is new; `find_candidates()` stays the over-finder. The note guard, the resolver's contract narrowing and the question fingerprint still need every span.
- **Applied once.** Cleaning happens in the graph's `find_candidates` node, so the fast path, Jev, Laya, the LLM, the mapper and the feed guard all see the same object. Candidate ids keep their original numbering.
- **Noise is dropped.** Tickers and numbers inside `<@…>` / `<#…>` / `<:emoji:…>`, URLs and `hh:mm` times are removed.
- **Numbers are capped.** Only the first `MAX_NUMBERS` (12) are asked about; the rest are counted in `numbers_dropped`. When `numbers_dropped > 0`, the mapper adds a slow-path reason for any actionable type. A feed-only reading can still settle.
- **Ticker candidates are not filtered by the optionable list.** This was built and measured, then removed. On the dev split (289 labelled messages, real Jev, everything else equal):
  - **Feed-only:** 60 with the filter, 68 without; the filter lost 8.
  - **Assemble:** 17 with the filter, 19 without.
  - **Slow path:** 205 with the filter, 195 without.
  - **Why:** with the junk-word candidates removed, Jev's chatter confidence fell from about 0.93 to 0.76–0.89, which is below `t_chat`. Jev seems to treat a cloud of word candidates as a sign of chatter.
  - TRD §16 C86 records the check.

## Where
| Area | Change |
|---|---|
| `interpret/candidates.py` | `clean_candidates`, `MAX_NUMBERS`, `Candidates.numbers_dropped` |
| `interpret/jev/mapping.py` | `numbers_dropped` sends actionable types to the slow path |
| `graph/message_graph.py` | the node cleans the candidates |

## Tests
- `tests/unit/interpret/test_candidates.py`: tickers are not list-filtered; noise is dropped; the cap counts the dropped numbers.
- `tests/unit/interpret/test_jev_mapping.py`: dropped numbers never let an actionable message settle.
