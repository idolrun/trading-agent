# F083 — Trading reports API (`/api/v1/reports/trading/*`)

## Objective
Answer, from real records only: how many signals came in, what the decision engine and the person did with them, what was ordered and filled, and whether the executed trades made or lost money. Read-only: nothing here submits, changes or cancels an order. No mock data, no figure that cannot be traced to a row.

Frontend: [F084](F084-frontend-trading-reports.md).

## Source of truth per fact
| Fact | Where it lives | Notes |
|---|---|---|
| Signal received / parsed | `signals` + `message_events.posted_at` | event time = the message's `posted_at` |
| Engine accepted / rejected | `cards` | accepted = a card with `card_type <> 'disabled'`; rejected = `card_type = 'disabled'` (+ `disabled_reason`); none = no card |
| Person approved / rejected | `approvals.decision` | an expired or invalidated card has no approval; its reason is the `card_events` row |
| Order submitted | `orders` (`status <> 'intent'`) | entry/exit orders only; protective legs are not counted as separate submissions |
| Fills | `fills` | the only source of executed quantity and price |
| Position and cost basis | `lots`, `lot_events` | weighted average, not FIFO; a sell leaves the average unchanged |
| Realized P&L | `lot_events` (`fill_reduce`, `close`) ⨝ `fills` | per reduction, see below |
| Unrealized P&L | open `lots` × a fresh quote | now only; not stored |
| Fees, commissions | **not stored** | every fee and net-of-cost field is `null` and `costsIncluded` is `false` |

## Stages are never merged
received → parsed → actionable → **accepted** (engine) → **approved** (person) → **ordered** → **filled** → closed → P&L known. Each count comes from its own record: an accepted signal is not an approved one, an approved one is not an order, an order is not a fill, and a rejected signal has no P&L. "Not actionable" = `chatter`, `commentary_pnl`, `tp_hit_notice`, `out_of_scope_strategy`, `watch`.

## Formulas
- **Realized (one reduction)** = (fill price − `lot_events.avg_before`) × (`qty_before` − `qty_after`) × multiplier. Multiplier 100 for options, 1 for stocks (`ledger.lots.OPTION_MULTIPLIER`).
- **Realized (a lot)** = sum over its reductions, lifetime. **Realized (a period)** = sum of reductions whose fill time is in the period. If any `avg_before` is unknown the result is `null`, never 0.
- **Unrealized** = (quote mid − lot average) × open qty × multiplier; `null` when the quote is missing/stale or the lot has no average. Total is `null` if any open lot is unpriced (and `coverage.unpricedLots` says how many).
- **Net** = realized + unrealized, before costs. A closed trade's net is its realized.
- **Outcome**: `open` while any quantity is held (never final), else `profit` / `loss` / `breakeven` by the sign of realized, `unknown` when realized is `null`.
- **Win rate** = profitable / (profitable + losing + break-even) over lots *closed in the period* with a known result. Open and `unknown` lots are excluded.
- **Profit factor** = gross profit / |gross loss|; `null` without a loss. **Average hold** = `closed_at − opened_at`.
- **Average fill price** = quantity-weighted over fills (`Σ qty·price / Σ qty`).
- **Notional** = Σ price × qty × multiplier of fills.
- Money is `Decimal` end to end and serialized as a string. Nothing uses floating point on the server.

## Reporting periods
Boundaries are `America/New_York` calendar days (the domain clock), start-inclusive, end-exclusive; a DST day is 23 or 25 hours.

| `period` | Meaning |
|---|---|
| `1d` | today so far |
| `3d`, `7d`, `30d` | the last N ET calendar days, including today (a calendar window ending now, not a rolling 24h·N span) |
| `all` | everything on record, from the first fill or message; **computed from history, never by adding presets** |
| `custom` | `startDate` … `endDate` (ET dates, both included) → `[start 00:00, endDate+1 00:00)`; `endDate < startDate`, a missing date, or a span over 1830 days is `422 invalid` |

The previous window is the same length shifted back by the window's whole days (none for `all`). Chart buckets: `hour` (≤1 day), `day` (≤62 days), `week` (≤400), `month`. Unrealized P&L and open-position counts are always *now*, never a historical value at period end; the response says so (`unrealizedAsOf`).

`mode` selects `orders.mode` (`shadow` simulated, `paper`, `live`, or `all`). Omitted = the current `trading_mode`, so simulated and real results never mix by accident.

## Signal → trade attribution
`signal → cards.signal_id → orders.card_id (kind entry) → orders.lot_id`. A lot's result is credited to a signal only when that signal is the **single** source of the lot: no add from another signal, no manual or system entry, not a foreign lot. Otherwise the signal is counted under `attributionUnavailable` and the lot's P&L is not split or repeated. Auto-exits have no card and are tied only by `lot_id`. A profitable position does not prove the signal caused the profit. Rejected signals never carry P&L and no hypothetical P&L is computed.

## Endpoints
All `GET`, session cookie required (`401 unauthorized` otherwise), camelCase JSON. Shared query parameters: `period` (default `7d`), `startDate`, `endDate`, `mode`, `symbol` (exact underlying), `instrument` (`option`|`stock`), `providerId`. There are no roles or accounts: every active user sees the one book (PRD §13); `providerId` is a filter, not an authorization boundary.

| Path | Extra parameters | Returns |
|---|---|---|
| `/overview` | | counts with previous-period comparison, executed trades, realized / unrealized / net, win rate, averages, profit factor |
| `/signals` | `engine`, `page` | funnel, engine and approval distributions, reasons (engine, you, lifecycle, broker), by symbol, by provider, conversion, accepted-signal outcomes, paged signal log |
| `/orders` | `side`, `status`, `page`, `pageSize` | status counts, requested vs filled quantity, notional per bucket, per-symbol fills, broker rejections, paged orders |
| `/pnl` | | realized, unrealized, gross profit/loss, win/loss counts, averages, profit factor, hold time, cumulative + per-bucket series |
| `/instruments` | `sort`, `order`, `page`, `pageSize` | per (underlying, instrument) rollup; `lowSample` under 5 closed trades |
| `/trades` | `state`, `outcome`, `search`, `sort`, `order`, `page`, `pageSize` | one row per lot, server-filtered, sorted and paged |
| `/trades/{lotId}` | | signal, parsed fields, decision, approval, orders with status steps, every fill, position changes, stops, audit entries, outcome explanation (`404` if unknown) |
| `/timeseries` | `metrics` (comma list) | one point per bucket, `hasActivity` marks empty buckets |

Every response carries `coverage`: window and label, mode, `asOf`, `firstEventAt`, `costsIncluded`, `unpricedLots`, `lotsWithoutBasis`, `reconcileAdjustments`. Anything unknown is `null`; the UI shows "—", never 0.

Code: `app/reports/{period,pnl,queries,service}.py`, `app/api/routes/reports.py`, `app/api/schemas/reports.py`. Layering: route (validation, auth, serialization) → `service` (assembly, valuation) → `queries` (SQL only) with `pnl`/`period` as pure functions.

## Database
Migration `0030_report_indexes`: `ix_fills_filled_at` and partial `ix_lots_closed_at` (windowed by time). No new tables: the reports read the existing ones.

## Limitations (stated, not hidden)
- **Fees and commissions are not captured** anywhere (`fills` has no fee, broker events carry none). All P&L is before costs.
- **No history of marks.** Unrealized P&L and any equity curve cannot be reconstructed for a past date.
- **Reconciliation changes** (`reconcile_adjust`, `adopt_foreign`) have no fill, so they are not in realized P&L; the count is reported.
- Decision-engine rejection has no dedicated record: it is read from the disabled card. Signals that never get a card appear as "no card".
- The broker's free-text rejection reason is not stored; only the broker status recorded with the rejection is reported.
- Orders and fills have no `provider_id`; provider filters join through the lot or the card.
- Average-cost basis differs from a broker's FIFO/tax-lot figure (TRD §16.2 #10).
- The trade list is lot-based. Rejected, expired or never-filled signals have no lot and are in the signal log and the order activity table.
- Aggregates that reach Python (closed trades in a window, open lots) are narrow rows for one user's book; move them into SQL if the book grows by orders of magnitude.

## Tests
- `tests/unit/reports/test_period.py`, `test_pnl.py`: windows (ET, DST, custom errors, previous window) and the arithmetic.
- `tests/integration/api/test_reports_api.py`: a five-lot book with hand-computed results (average cost over two entry fills, partial then final exit, option ×100, stock loss, open lot with a quote, 40-day-old and shadow lots); periods, custom range, mode filter, missing quote → unknown, unknown cost basis → unknown, partial fills and unfilled orders, pagination and sorting, 401 on every route, empty book.

```bash
cd backend && docker compose -f ../compose.yaml up -d postgres
uv run pytest tests/unit/reports tests/integration/api/test_reports_api.py
uv run ruff check . && uv run mypy app
```
Tests use `TEST_DATABASE_URL`; to avoid colliding with another session, point both `TEST_DATABASE_URL` and `TEST_DATABASE_OWNER_URL` at a separate database.
