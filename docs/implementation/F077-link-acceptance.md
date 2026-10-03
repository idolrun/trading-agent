# F077 — Deterministic link acceptance: a model proposes, code resolves

## Objective
A follow-up links to a provider position only on code evidence. A model's `links_to` pick, however confident, is recorded as a proposal for the review card and never turns an ambiguous follow-up into a link.

## Before (audit H3)
- `correlation.resolve_link` accepted the interpreter's hint when it was one of the resolver's candidates, ≥ `t_link`, and consistent with the stated contract.
- Example: SPY 765P and QQQ 745P open, "trim half", Jev picks QQQ at 0.84 → linked. Nothing in the text supports that pick.
- One open position plus "trim half": the resolver said ambiguous, then Jev's confidence "upgraded" it to a link. So model confidence was the thing that made the link.

## Rule (user decision 2026-10-03)
Evidence, strongest first (`ledger/resolver.py`):
1. reply chain;
2. thread;
3. stated contract;
4. **sole candidate**: one live lane position left after rungs 1–3, either because the lane holds only one or because "calls" narrowed the book to one. Outcome `unique`, `method = code`, evidence `rule = sole_candidate`.

This reverses the 2026-10-02 decision ("one open position is not evidence"), as the user asked.

`correlation.resolve_link`, for follow-ups:
- A resolver `unique` pick links if no contract field the message states contradicts it. A contradiction is ambiguous; a user's choice still overrules.
- Anything else is **AMBIGUOUS**: a review card listing the candidates.
- When the hint names a position, it is stored as `evidence.proposal = {target, confidence, by}`:
  - `rule = model_proposed` when the pick would have been acceptable;
  - `rule = unresolved` with `proposal.refused` (not in the lane's book / not a resolver candidate / below t_link / contract conflict) when it would not.
- There is no path from a hint to `LINKED`.

States, without new DB enums, in `message_links`:

| Spec state | Stored as |
|---|---|
| UNIQUE | outcome `linked`, method `reply` / `thread` / `contract` / `code` (sole candidate) |
| MODEL_PROPOSED | outcome `ambiguous`, evidence rule `model_proposed` + `proposal` |
| AMBIGUOUS | outcome `ambiguous`, evidence rule `unresolved` |
| UNRESOLVED | outcome `none` (empty lane book) |

## Not done (from the plan)
- **"Literal-contract narrowing" after interpretation is dropped.** The resolver already narrows on every text-literal contract token (tickers, strike-shaped tokens, call/put words). The only extra fields would be a bare-number strike or an underlying, and choosing their role is itself a model judgment. Expiry-only discrimination is left to review.

## Effect
- The fast path's follow-up rules (`_trim_or_exit`, `_stop_breakeven`) fire on any `unique` resolution. Sole-position follow-ups ("trim half", "out runners", "stop breakeven") are now settled by code without a Jev call.
- `Resolution.unresolved` (replay) of a one-position book is the sole candidate, as live.

## Files
- `backend/app/ledger/resolver.py`: rung 4 and `_sole`; `unresolved` with one position.
- `backend/app/ledger/correlation.py`: the hint accept path removed; `_proposal` records the pick.
- `docs/trd.md` correlation order.

## Tests
- `tests/unit/ledger/test_resolver.py`:
  - sole candidate;
  - unresolved book of one;
  - "calls" narrowing to one;
  - two calls stay ambiguous.
- `tests/unit/ledger/test_correlation.py`:
  - confident pick between two positions is only a proposal;
  - sole position linked by code whatever the hint;
  - refused proposals (outside the book, not a candidate, below t_link, contract conflict);
  - sole candidate contradicted by a stated strike / right / ticker / instrument → ambiguous;
  - contract resolution linked.
- Integration:
  - `test_lanes.py`: another channel's position named by the model → refused, the lane's sole position linked.
  - `test_missing_underlying.py`: two live MU 118C → "trim half" is a review with the proposal recorded.
  - `test_out_of_order.py`: late entry → sole position re-link proposed whatever the hint; three positions → unchanged.
  - `test_multichannel_scenario.py`: all seven messages settled by code.
