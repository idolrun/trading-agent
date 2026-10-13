# F090 — Clean candidates before Jev

## Objective
Implements ranked fix #2 from `docs/reviews/signal-bottleneck-2026-10-12.md` §4.4. The ticker candidates were mostly ordinary English words ("s, while, can, t, …", 3.5 per message on average). The number candidates reached 65 on a single message. Every candidate adds a choice, and every number adds a `role_nN` question that must clear `t_field`, so most messages failed to settle.

## Decisions (2026-10-13)
- **New function, old one unchanged.** `clean_candidates()` is new; `find_candidates()` stays the over-finder. The note guard (invented tickers or numbers), the resolver's contract narrowing and the question fingerprint still need every span.
- **Applied once.** Cleaning happens in the graph's `find_candidates` node, so the fast path, Jev, Laya, the LLM, the mapper and the feed guard all see the same object. Candidate ids keep their original numbering, so gaps can appear.
- **Ticker rules:**
  - Always kept: `$TICK` and ALL-CAPS.
  - A lowercase or mixed-case word is kept only when it is on the optionable list or is a symbol-map key, value or provider alias. It is still dropped if it is a single letter or in `COMMON_WORDS`.
  - Tickers inside mentions or URLs are dropped.
  - When the list is unavailable (not loaded within 0.2 s, or the load failed), nothing is filtered; an empty list is never used.
- **Number rules:**
  - Dropped: numbers inside `<@…>` / `<#…>` / `<:emoji:…>`, URLs, and `hh:mm` times.
  - Only the first `MAX_NUMBERS` (12) are asked about; the rest are counted in `numbers_dropped`.
  - When `numbers_dropped > 0`, the mapper adds a slow-path reason for any actionable type. A feed-only reading (a recap full of numbers) can still settle.
- **Where the list comes from:** the optionable list behind the F086 cache (24 h), shared with validation.
- **Replay:** the replay uses a snapshot of Alpaca's list (`app/evalkit/optionable_symbols.txt`, 6,306 symbols, fetched 2026-10-13), so it can measure this change.
- **TRD §16 C86** is reworded: the list now filters candidates; validation is unchanged.

## Where
| Area | Change |
|---|---|
| `interpret/candidates.py` | `clean_candidates`, `MAX_NUMBERS`, `COMMON_WORDS`, `Candidates.numbers_dropped` |
| `interpret/jev/mapping.py` | `numbers_dropped` sends actionable types to the slow path |
| `graph/message_graph.py` | `GraphDeps.symbols`; the node cleans, with a 0.2 s shielded wait for the list |
| `main.py` | `cached_md` shared by validation and `symbols` |
| `evalkit/cli.py`, `evalkit/optionable_symbols.txt` | the replay uses the snapshot |

## Tests
- `tests/unit/interpret/test_candidates.py`:
  - the report's sentence keeps no ticker;
  - lowercase listed symbols are kept, `$T` is kept, ALL-CAPS is kept;
  - aliases are kept;
  - no list means no filtering;
  - mentions, URLs and times are dropped;
  - the cap counts the dropped numbers.
- `tests/unit/interpret/test_jev_mapping.py`: dropped numbers never let an actionable message settle.

## To measure (Milestone A gate)
Run the F082 replay on `trading_measure` before and after. Wrong links and false feed-only must stay 0. Also watch the review rate, the ticker/role doubt reasons, and the Jev request token count.
