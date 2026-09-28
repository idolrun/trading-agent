# F024 — Decide: Risk Limits, Staleness & Warnings

## Objective
Complete the pure decision engine with the safety gates that turn an entry into a disabled card or suppress it: risk limits, config completeness, kill switch / entry block, staleness, plus the late badge and opposite-direction warning.

## Requirements covered
- Decision log: C23.
- PRD FR-18 (max daily loss realized + unrealized, max open positions, max total exposure; foreign positions count), C75 (limit hit → disabled card with reason; no push), FR-19 + C74 (opposite-direction warning), FR-1c + C90 (stale entry/add → ledger only, feed "stale", no approvable card; exits/trims/stops/corrections carded with "late" badge), AC-19, 7.9 (kill switch pauses new entries), §11 (reconciliation mismatch blocks new entry cards), PRD (e) (no money defaults).
- TRD §2.4 staleness, §6 risk limits.

## Dependencies
- Depends on: F023
- Produces: stages appended in `decide()`: `apply_staleness`, `apply_entry_gates`, `apply_warnings`; `RiskSnapshot` builder (service, I/O) `risk.snapshot(session, marketdata)`.
- Used by: F026, F036 (revalidate re-runs gates), F047 (manual orders pass same checks, C42).

## Scope
Order of stages after F023 output:
1. **Staleness (FR-1c):** `stale` and action ∈ {entry, add} → drop the card, return `FeedLabel("stale")` (ledger was still updated by F026). Other actions with `stale` or `late` → add label `late`.
2. **Entry gates (entry/add only), first failure wins → `card_type=state=disabled` with reason:**
   - `missing_for_entries(config)` non-empty → "config incomplete: …";
   - `kill_switch` active → "kill switch active" (**OD-27** proposal: kill switch disables entry/add cards only; exit/stop cards still created so exits are never missed — confirm);
   - `entries_blocked` active → "entries blocked: reconciliation mismatch";
   - daily loss (realized today + unrealized, incl. foreign) ≥ `max_daily_loss_usd` → "max daily loss reached";
   - open positions (lots open incl. foreign) ≥ `max_open_positions` → "max open positions";
   - exposure (cost basis of open lots incl. foreign) + `qty × limit × 100` > `max_total_exposure_usd` → "max exposure" (no automatic down-sizing — not specified).
3. **Warnings:** opposite-direction lot on the same underlying (call vs put, any provider) → warning (C74); validation warnings copied.
- `RiskSnapshot` (service): realized P&L today from fills (ET trading day), unrealized from quotes of open lots; missing quote → unrealized unknown → entries disabled "risk unknown: quote missing" (never assume 0).
- PDT counter: **not** implemented until OD-3 decides account type (PRD §15).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/decide/risk.py` | pure gates + warnings | I/O |
| `backend/app/decide/risk_snapshot.py` | builds `RiskSnapshot` from DB + quotes | decisions |
| `backend/tests/unit/decide/test_risk.py`, `backend/tests/scenarios/test_staleness.py` | tests | — |

## Files to modify
`backend/app/decide/engine.py` (append stages).

## Tests
- Entry processed 45 s after post (`stale_entry_s=30`) → no card, feed label stale; exit processed 45 s after → exit card with `late` (AC-19).
- Each limit individually → disabled with its reason; foreign lot counted.
- Kill switch → entry disabled, exit still carded.
- Opposite-direction warning present.
- Missing quote for an open lot → entries disabled "risk unknown".

## Acceptance criteria
AC-19 at decision level; FR-18 limits proven with foreign positions; disabled cards carry reasons.

## Implementation steps
1. Staleness stage. 2. Gates. 3. Warnings. 4. Snapshot builder. 5. Tests.

## Open decisions touched
OD-27, OD-3 (PDT).

## Implementation notes (2026-09-29)
- `DecisionInputs` gained `stale`, `late`, `flags: FlagsView(kill_switch, entries_blocked)` and `risk: RiskSnapshot | None`; `decide()` = F023 `_propose` → `apply_staleness` → `apply_entry_gates` → `apply_warnings` (`decide/risk.py`).
- Stale entry/add → **no card** (`[]`); the "stale" feed label is the inbox status F026 sets from `ProcessingContext.stale`, so no separate `FeedLabel` type.
- Gate order: config incomplete → kill switch → entries blocked → risk unknown (no snapshot / quote missing / cost basis or realized avg missing) → daily loss (`-(realized + unrealized) ≥ max`) → open positions → exposure. Gate reasons override F023's "tier cannot buy one contract".
- **Max open positions applies to `entry` only**: an add grows an existing lot and opens nothing. Exposure is checked only when qty and limit are known (F036 re-runs the gates after an edited qty).
- Opposite-direction warning (C74) goes on entry/add cards only.
- `build_risk_snapshot(sessions, marketdata, now)`: reads lots + today's (ET midnight) sell fills in one read-only session, closes it, then fetches quotes. Realized P&L = Σ (fill price − `lot_events.avg_before`) × qty sold × multiplier, via new `ledger.repo.sells_since`.
- Test fixtures shared through `tests/decide_helpers.py`; tests in `tests/scenarios/test_staleness_and_risk.py` (the doc's `unit/decide/test_risk.py` + `scenarios/test_staleness.py` in one file) and `tests/integration/decide/test_risk_snapshot.py`.
