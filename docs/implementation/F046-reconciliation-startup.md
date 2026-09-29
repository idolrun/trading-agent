# F046 — Reconciliation & Startup Sequence

## Objective
Treat the broker as the source of truth: reconcile lots and orders against the broker at startup, periodically and after fills; block entry cards on any mismatch; and run the TRD §9 eleven-step startup sequence so a crash or failover never duplicates cards or orders.

## Requirements covered
- Decision log: C46.
- PRD §11 (reconcile lots; mismatch → flag + block entry cards until resolved; approval cards open at crash time expire; adopt working broker orders, do not resubmit; reprocessing never duplicates), §12 durability (after failover/restore reconcile before entries), C47 (foreign positions), AC-7, AC-23.
- TRD §9 startup sequence 1–11, periodic reconciliation every 60 s + after every fill, recovery by `client_order_id` prefix.

## Dependencies
- Depends on: F008, F014, F027, F036, F040, F043, F044, F045
- Produces: `Reconciler.run() -> ReconcileReport`, `startup()` orchestration in lifespan, periodic task, lot adjust endpoint support (used by F047).
- Used by: F047, F063, F064 runbooks.

## Startup sequence (lifespan, in order; each step logged + timed)
1. `FlagService.set('entries_blocked', True, reason='startup')`.
2. Load broker positions and open orders.
3. Adopt working orders: match by `client_order_id`/`broker_order_id` → update DB status; broker orders with our `cta-` prefix but no DB row → urgent alert + keep entries blocked (never recreate intents without an approval row).
4. Reconcile lots (below); mismatch keeps entries blocked.
5. Resume every interrupted approval thread with `{"decision":"expire","reason":"restart"}` after transitioning its card to `expired` (PRD §11).
6. Reset inbox `processing` → `received` (F012 also does this; idempotent).
7. Gap detector `run_once()` (F014).
8. Reload timers; fire overdue (F027); schedule today's cutoffs (F045).
9. Resubscribe quotes; restart underlying-stop watcher (F044) and exit reprice loops for adopted working exits (F043).
10. Start dispatcher (catch-up mode applies automatically, F013).
11. If reconciliation clean → clear `entries_blocked`.

## Reconciliation rules
- Per `broker_symbol`: `broker_qty` vs `Σ open lot qty` (tracked + foreign).
  - No system lots and broker qty > 0 → create/adjust **foreign** lot (C47) — not a mismatch.
  - System (non-foreign) lots exist and quantities differ → **mismatch**: urgent alert with details, `entries_blocked` stays/becomes active.
  - Lot open with broker qty 0 → mismatch.
- Orders: DB `working` but broker says filled/cancelled → apply status (fills via F040 path); DB `unknown` → query by client id.
- Clean run → clear `entries_blocked` (only if its reason was reconciliation/startup).
- Resolution paths: broker-side fix, manual exit, or `reconcile_adjust` of a lot to broker qty (F047, step-up, audited).
- Periodic: every 60 s in market hours + post-fill hook (debounced 2 s).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/reconcile/service.py` | `Reconciler` (pure compare + apply) | order submission |
| `backend/app/reconcile/startup.py` | ordered startup steps | — |
| `backend/tests/integration/reconcile/test_reconcile.py`, `backend/tests/integration/graph/test_restart.py`, `backend/tests/unit/reconcile/test_compare.py` | tests | — |

## Files to modify
`app/main.py` (lifespan runs `startup()` in place of `dispatcher.start()`; reconciler on `app.state`, post-fill hook registered on the consumer's existing `on_fill` list, so `consumer.py` is unchanged), `app/cards/approval_service.py` (`resume_approved`), `app/execution/repo.py` (`orders_to_sync`, `open_exit_roots`, `known_client_ids`), `app/execution/intent.py` (`CLIENT_ID_PREFIX`), `app/system/flags.py` (`active_reason`), `app/broker/fake.py` (`seed_position`). Shadow mode is skipped (OD-33).

## Tests
- AC-7: kill (cancel tasks) after intent commit but before broker ack; restart → order adopted by client id; no duplicate submit; pending card expired.
- Broker qty ≠ lot qty → entries blocked, entry cards disabled "entries blocked"; exits still work; fixed → cleared.
- Broker position with no lots → foreign lot created, counted in risk limits.
- `cta-` order unknown to DB → alert, entries blocked.

## Acceptance criteria
AC-7 proven on fakes; AC-23 reconciliation-before-entries clause proven (failover itself measured in F063/F064).

## Implementation steps
1. Pure compare. 2. Apply results (foreign lots, flags, alerts). 3. Startup orchestration. 4. Periodic + post-fill triggers. 5. Tests.
