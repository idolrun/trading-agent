# F085 — The user types every quantity

## Objective
Every buy and sell card carries a required, editable, initially empty Quantity (shares for a stock, contracts for an option). Nothing chooses it: not size tiers, account-equity percentages, trim fractions, the LLM, a quantity stated in the signal, or a default. The backend refuses to approve or execute without a valid whole number, judges that number against the risk limits without changing it, and sends the broker exactly the approved quantity.

## Requirements covered
- PRD C105 (supersedes C15, C51, C76, C82), FR-17, §7.1 step 4–5, §7.3, §7.4, §7.8, AC-15, AC-16, AC-24.
- TRD §6 "No sizing".

## Decisions (confirmed 2026-10-11)
- Size tier config is removed entirely (`option_tiers`, `stock_tiers`); stored documents that still carry the keys load (retired keys are dropped on read).
- A provider "all out" / full exit becomes a `close` card ("Close entire position (N)"): no quantity field; its quantity is the open lot at approval, void if the lot changes before execution. Partial exits (trim, soft exit) need a typed quantity. Flatten, the end-of-day forced close, underlying stops, stop restore and exit re-pricing are unchanged.
- Take-profit levels are stored on the card (`tp_levels`); the equal split over the user's quantity is derived when it is typed and stays editable.
- `size_tier` is removed from the interpretation contract, the Jev question set (new pinned fingerprint `jev_questions@e12e3dadf071`), the fast path grammar, the LLM extraction schema and prompts. `qty_stated` is still parsed and kept as untrusted information; nothing reads it.

## Where
| Area | Change |
|---|---|
| `decide/engine.py`, `rules.py`, `types.py` | no `size`/`exit_qty`; `qty=None` for entry/add/trim/exit; guard fails if one leaves with a quantity; `tp_levels`; full exits → `close` |
| `cards/edits.py`, `api/schemas/card_decisions.py` | `qty` is a strict integer ≥ 1; legs derived from the typed qty |
| `cards/approval_service.py` | `_approve` requires `edits.qty` (non-manual buy/sell); `_qty_gate` runs the preflight on it and refuses with the reason (never resizes); close snapshots the lot; `lot_qty_changed` at revalidation |
| `cards/validity.py`, `execution/card_executor.py` | close handling; executor sends the approved snapshot qty |
| `runtime_config/schema.py` | tiers removed, retired keys dropped on read |
| `interpret/*`, `validate/normalize.py` | `size_tier` removed |
| Frontend `features/cards/*` | Quantity starts empty, unit by instrument, always sent, order value follows typing, live preflight on the typed qty blocks Approve with the reason, TP split follows qty, Close card; `features/manual` forms start empty; `features/config` tiers removed |

## Tests
- Backend: `tests/scenarios/test_decide_trade_patterns.py`, `unit/decide/test_rules.py`, `integration/api/test_card_decision_api.py` (missing/invalid/approved quantity, 409s), `integration/graph/test_entry_execution.py` (broker gets the typed qty, changed qty), `test_lifecycle.py` (close card, trim bounds), `test_paper_flow.py` (account refusal, exposure refusal, revalidation still guards), `unit/runtime_config/test_schema.py` (retired keys).
- Frontend: `card-inbox.test.tsx`, `manual.test.tsx`, `config.test.tsx`; e2e: `ac18`, `brackets`, `entry-trim`, `lifecycle`, `corrections`, `missing-underlying`, `zero-dte`.

## Follow-up: "approved with no quantity box" (2026-10-11)
- Cause: the signal `$TSLA 400C 10/16/26 / Entry / SL / TP1 / TP2` became a raw review card, not a trade card. The `1` of `TP1` and the `2` of `TP2` were offered to Jev as number candidates, the model was unsure what they were (< `t_field`), and any such doubt sends the message to review. Approving a raw review card only acknowledged it (no order), which looked like an accepted trade.
- Fix: `find_candidates` no longer offers a bare 1-2 digit integer glued to a letter (`TP1`, `T3`); `ApprovalService._approve` refuses to approve an unreadable-signal review card (422, `_unreadable`); the card shows only Dismiss and a link to Manual entry. Re-link and position-choice review cards (they carry a signal or candidate positions) are unchanged.
- Not measured: the F082 replay over the real corpus calls Jev live and was not run for this change.

## Out of scope / notes
- Replay parity over the real corpus (F082) was not re-run for the `size_tier` removal; do that before the next Jev re-pin.
- `adjust lot` (reconciliation to the broker's quantity) is not a trade card and keeps its own input.
