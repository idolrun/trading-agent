# F072 — Signal state: watch, conditional, re-entry

## Objective
Treat a provider's messages as events on an ongoing signal, not as independent signals. A watched or conditional setup becomes state without ever becoming a trade. A later entry opens that setup in place. "Back in" re-enters the position it refers to.

## Model (user decision: extend `provider_positions`, no new table)
```
watch ─▶ conditional ─▶ open ─▶ trimmed ─▶ closed
  └───────────────────────────▲         (a re-entry opens a NEW row: reentry_of_position_id → the closed one)
```
| Area | Change |
|---|---|
| `ledger/models.py` + migration `0024` | `PositionStatus` + `watch`, `conditional`. `PositionChange` + `watch`, `condition_set`. `instrument` is nullable and the contract may be partial for setups only (`contract_shape`, `instrument_unless_setup`). New columns `condition_text`, `reentry_of_position_id` (self FK). Lots keep the strict contract shape. |
| `ledger/positions.py` | `SETUP_TYPES`, `LIVE`, `SETUPS`. `_setup()`: a watch/conditional opens or restates a setup (no underlying → no effect). An entry linked to a setup → `OPEN` on the same row (keeps `condition_text`). Trim / exit / stop / add on a setup → `InvalidTransition` (review). |
| Book reads | `context_repo.lane_book_filter(lane, as_of)`: live positions plus the setups opened on the message's ET day (a setup lasts one day). Used by `open_book` and `lock_open_book`, so apply's version check covers setups. `ledger.repo.reentry_pool`: the lane's positions closed today plus today's setups, with each one's opening message. |
| Linking (`ledger/correlation.py`) | An entry: C62 add on a live contract first; else the one setup it fits (same underlying, contradicts nothing it named) → `LINKED` (`setup`); two fitting setups → a new position, never a guess. A watch/conditional: restates the one setup on that underlying, else a new setup. The cross-check treats fields a setup never named as unknown. |
| Resolver | Follow-ups resolve against live positions only. When the text says "back in / re-entry / in again / reload", `Resolution.reentry` lists the `Prior`s (closed today + setups). |
| Fast path | `_reentry`: a whole-message "back in [here] [@ price]" with exactly one complete prior gives an entry. The contract is cited as `context` from the prior's opening message, so the card is **inferred** (one-click confirm, C32). The price is the text's own. A closed prior records `field_sources.reentry_of`. Several priors, or a setup that never named its contract → Jev → review. |
| Planner / apply | Splits the lane book into live and setups. A conditional with no words of its own stores the message text as `condition_text`. `SignalOutcome.reentry_of` → `ledger.record(reentry_of=…)`. Never scales follow-up prices against a setup. |
| Decide | An entry on a setup is an `ENTRY` card, not an `ADD`. Watch and conditional stay feed only (no card). |
| Jev mapping | A `watch` keeps its confidently picked ticker. A `conditional` is not sent to the slow path for having a condition (the condition is the message). |
| API / UI | `PositionOut.instrument` is nullable; `condition_text` and `reentry_of_position_id` are added. The positions filter lists `watch` and `conditional`, and a setup's label shows what it named. |

## Not done (PARTIAL)
- Setups are not closed at the end of the day; they leave the book (and the re-entry pool) after their ET day.
- `invalidated` is not a status: a deleted setup message is handled like any delete (7.5: the ledger is left as is).
- "Back in" read by Jev (not the fast path) still goes Jev → review. Only the fast path builds a re-entry.
- A conditional trigger ("calls now" after "if it holds 770 then calls") is an entry that must name its contract, or it goes to review.

## Tests
- `tests/unit/ledger/test_setups.py` (14): setup transitions; setup → open in place; no trim/stop/add on a setup; entry fits one setup / contradiction / two setups; restatement; re-entry rule (one prior, two, incomplete setup, setup without a pointer).
- `tests/integration/graph/test_signal_state.py`:
  - watch → conditional → entry is one row: WATCH, CONDITION_SET, OPEN events and an ENTRY card;
  - a trim replying to a watch leaves it untouched;
  - entry → out → "back in 2.10 @everyone" → a new row re-entering the closed one with the same contract and an INFERRED entry card;
  - two closed positions + "back in" → review.
