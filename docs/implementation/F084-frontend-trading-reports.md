# F084 — Trading Performance page (`/reports/trading`)

Depends on [F083](F083-trading-reports.md). Authenticated like every desk page (`app/(desk)`), linked from the sidebar as **Reports**.

## Layout
Title **Trading Performance**; filter bar (period 1D · 3D · 1W · 1M · Overall · Custom, mode, instrument type, symbol, Refresh); six tabs, each fetched only when opened:

| Tab | Content |
|---|---|
| Overview | eight KPI cards (signals, accepted, rejected, executed trades, realized, unrealized, net, win rate), secondary stats, "How to read these numbers" |
| Signals | stage funnel, engine decision, your decision, reasons, accepted vs rejected over time, by symbol / provider, accepted-signal outcomes with the attribution note, signal log |
| Execution | order counts and fill rate, status mix, buy vs sell fills, notional, quantity by instrument, broker rejections, order activity table |
| P&L | cumulative realized line (break-even baseline), per-bucket bars, win/loss split, wins vs losses over time, net by instrument, costs |
| Instruments | sortable rollup; a symbol opens its trades |
| Trades | one row per position; search, status, result filters; server sort and pages; **Inspect** opens the lifecycle sheet |

## Design choices (UI/UX Pro Max guidance applied)
- Dense dashboard on the existing tokens (`--success`, `--destructive`, `--info`, `--warning`); no new palette. Tabular numerals for every figure.
- Gain/loss is never colour alone: sign (+/−), arrow glyph and a screen-reader word (profit/loss/break-even). Loss bars are hatched.
- Line for the cumulative trend, bars for per-bucket results, horizontal bars for reasons and instruments. No pie or candles; the funnel is a labelled list of bars.
- Every chart has a text summary (`role="img"`) and its numbers in a **Data table** disclosure.
- Unknown values show "—" with a reason; an open trade is labelled "Open · not final".
- Loading skeleton, empty state ("Nothing here is sample data"), error with Retry, "showing the previous result" while a period changes (`keepPreviousData`).
- Native `select` / `date` inputs; keyboard-sortable headers with `aria-sort`; 24px+ targets; reduced-motion respected on the refresh spinner. Tables scroll horizontally on narrow screens.

## Data
`features/reports/queries.ts`: one TanStack query per section, key `["reports", …]`, `staleTime` 30 s, no polling. Refresh invalidates the whole `["reports"]` tree. A custom range or invalid symbol disables the query instead of sending it. Types come from the generated schema (`pnpm gen:api`).

## Tests
`features/reports/reports.test.tsx` (vitest + msw): each period sends the right parameters; custom range waits for both dates; mode / instrument / symbol; sign + glyph on gains and losses; unavailable shown as "—" never `$0.00`; empty and error states; P&L tab; trade table sort / filter / search / paging parameters; the lifecycle sheet with each fill; instruments → trades hand-off. Charts are asserted through their text summaries and data tables because jsdom has no layout.

```bash
cd frontend && pnpm gen:api && pnpm exec tsc --noEmit && pnpm lint && pnpm test features/reports
```

## Not built
Filters in the URL (state is in memory), CSV export, a mobile-specific card layout for the tables (they scroll), end-of-period historical unrealized P&L (not recorded).
