# F069 — Signal context resolver + deterministic cross-check of AI links

## Objective
Code decides which of the lane's live positions a follow-up can be about, before any model is asked. A model pick is accepted only when code agrees with it. A wrong signal association becomes a review card, never a link.

## Before (audit)
- Correlation was: reply parent (one hop), then Jev `links_to` at confidence ≥ `t_link`, then review.
- Jev was offered every position of the provider (across channels before F068).
- A confident pick was accepted even when the message named a different ticker.

## What changed
| Area | Change |
|---|---|
| `app/ledger/resolver.py` (new, pure) | `resolve_context` → `Resolution{outcome: unique|ambiguous|none, position_id, candidates, method, evidence}`. Rungs: reply chain (≤ `REPLY_DEPTH`=3 hops, this provider's links only), thread (this provider's earlier links in the same `thread_id`), stated contract (`Evidence.of`: tickers, strikes, strike-shaped `765p` tokens, call/put words, from `find_candidates`). A narrowing never empties the set, so over-found words are not evidence. A reply/thread pick that the message's own contract words contradict → ambiguous. `ParentLink` moved here. |
| `ledger/repo.py` | `load_resolution` (DB side), `reply_to_of`, `thread_positions`. |
| `ledger/correlation.py` | `LinkInputs.resolution`. Follow-ups: a deterministic UNIQUE wins (still cross-checked); else the hint must be ≥ `t_link`, in the candidates and pass `inconsistency()` (underlying/expiry/strike/right/instrument). `Resolution.chosen` (a user's choice) links even over a misread field and records the conflict. `LinkDecision.evidence`. |
| Message graph | New `resolve_context` node after `load_context`. It narrows `ProviderContext.link_candidates`, the only `links_to` options for Jev/Laya and the LLM's `_link`. `decide_apply` passes the resolution to `Planner.plan`. Replay has no states, so it uses "every book position a candidate". |
| Relink / corrections | `RelinkService._plan(resolution=None)` resolves like the live pipeline; `apply_approved` uses `Resolution.chosen`. |
| Migration `0021` | `message_links.evidence jsonb`; link methods `thread`, `contract`. |

## Tests
- `tests/unit/ledger/test_resolver.py` (18): every rung, depth limit, other provider / closed position / other lane not evidence, contradiction, over-found words, strike-shaped token, "calls" only narrows, JSON round trip.
- `tests/unit/ledger/test_correlation.py`: hint contradicting the ticker / strike / right / instrument → ambiguous; hint outside the candidates → ambiguous; a consistent hint → linked; reply contradicted → ambiguous; user choice overrules.
- `tests/unit/interpret/test_jev_mapping.py`: `links_to` options are the resolver's candidates only.
- `tests/integration/graph/test_resolution.py`:
  - "trim half" with SPY + QQQ → a review listing exactly those two;
  - "trim SPY half" with a 0.99 model pick of QQQ → SPY linked (`contract`), QQQ untouched;
  - entry ← comment ← trim → `reply` at depth 2;
  - thread → `thread`.

## Limits
- Ticker evidence compares the message token with the position's stored underlying after the symbol map. "SPX" in a message does not match an `SPXW` position (it only fails to narrow).
- A multi-signal message shares one resolution; each signal is still cross-checked against its own stated fields.
