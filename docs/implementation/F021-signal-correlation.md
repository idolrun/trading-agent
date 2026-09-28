# F021 — Signal Correlation Resolver

## Objective
Decide, by code, which provider position a signal refers to — using reply-to deterministically, then the model's `links_to` hint above a threshold, and otherwise producing an explicit **AMBIGUOUS** (or NONE) outcome that becomes a review card. Never guess, never cross providers.

## Requirements covered
- PRD FR-3 (reply-to used deterministically), 7.3 step 1 (reply parent, author's open positions, recent messages), §6 (each signal linked to at most one provider position; link records method + confidence; never crosses providers), §10 (ambiguous target → review card), C31, C71, AC-18.
- TRD §8.1 correlation order, §4.4 (`links_to = ambiguous` or confidence < `T_link` → review; no model upgrades this), §12 sequencing table.

## Dependencies
- Depends on: F017, F020
- Produces: pure `resolve_link(signal, ctx: LinkInputs, t_link) -> LinkDecision`; `LinkInputs` loader (parent's current link, open book).
- Used by: F026 (writes `message_links`), F037 (re-check), F047 (user re-link).

## Identifier flow
```
message_id ──reply_to_id──▶ parent message_id ──current message_links row──▶ provider_position_id (must be same provider_id)
signal_id  ──LinkHint(target, confidence) from interpreter──▶ provider_position_id ∈ this provider's open book
```

## Scope — decision table
| Signal type | Condition (evaluated in order) | Outcome | method | confidence |
|---|---|---|---|---|
| entry / stock_entry | open position of same provider on the same contract exists | `linked` (becomes add, C62 / 7.1 step 2) | `code` | 1 |
| entry / stock_entry | otherwise | `new_position` | `code` | 1 |
| follow-up (add, trim, full_exit, soft_exit, stop_*, tp_*, tp_hit_notice, stock_exit, correction) | `reply_to_id` parent is linked to a position **of the same provider** that is not closed | `linked` | `reply` | 1 |
| follow-up | hint target ∈ open book and confidence ≥ `t_link` | `linked` | `jev` / `llm` | hint |
| follow-up | hint `ambiguous`, or confidence < `t_link`, or target not in book | `ambiguous`, `candidate_position_ids` = open book | interpreter | hint |
| follow-up | open book empty | `none` | interpreter | hint |
- A reply to **another author's** message never links (read-only context). The replying author's signal is resolved against their own book only (TRD §12: "B replies 'same' to A1 → B gets its own provider position; A's ledger unchanged").
- `ambiguous` and `none` on an actionable follow-up produce a review card listing candidates (F023/F026); nothing is applied to any position until the user picks one (F036 edit `chosen_position_id` → new `message_links` row, `method='user_correction'`).
- No rule upgrades `ambiguous` to `linked` (not a single-open-position shortcut either — PRD §10: target ambiguous → review).

## Out of scope
Writing rows (F026). Re-checking after late arrival (F037).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ledger/correlation.py` | `LinkInputs`, `LinkDecision`, `resolve_link` (pure) | SQL |
| `backend/app/ledger/correlation_repo.py` | parent current-link query (`message_links` latest row by signal of parent message), open book | decisions |
| `backend/tests/unit/ledger/test_correlation.py` | decision table + TRD §12 sequencing rows | — |

## Tests (unit, then integration with F026 in M2)
- A3 "take half off" with book `[P_A_GOOGL]`, hint P_A_GOOGL 0.93 (`t_link` 0.8) → linked/jev.
- A holds GOOGL + META, hint ambiguous → ambiguous with both candidates.
- hint confidence 0.6 → ambiguous.
- Reply to own entry message → linked/reply even if hint says otherwise.
- Reply to other provider's message → ignored; B's own resolution.
- Entry on a contract already open for this provider → linked/code (add).

## Acceptance criteria
AC-18 linking clause at unit level; ambiguity always explicit; cross-provider linking impossible (code + DB FK).

## Implementation steps
1. Types. 2. Pure resolver. 3. Repo queries. 4. Tests.
