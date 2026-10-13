# F086 — Market data off the critical path

## Objective
Removes the 1–4 s validation tail that `docs/reviews/signal-bottleneck-2026-10-12.md` §7 measured (validate p50 21 ms, p90 951 ms, max 3.8 s) by fixing the market-data part of ranked fix #6. Also closes three quote-safety gaps found while reviewing Alpaca freshness.

## Decisions (2026-10-13)
- Caching stays in-process, behind the `MarketData` port (TRD §15.2). Redis is not added; it waits for a measured §15.2 trigger.
- **Option chains are cached and quotes are not.** A chain is reused for 45 s (`CHAIN_TTL_S`, allowed range 1–300) to prove the strike is listed and to feed the adjacent-strike warning. The quote a decision uses is always the contract's own fresh snapshot, never a chain row that could be up to a TTL old.
- **A cold chain never blocks.** Validation waits at most 0.3 s (`CHAIN_WAIT_S`) for the chain and then uses the contract's snapshot as proof the contract exists. The chain keeps loading in the background, one load per key. The adjacent-strike warning (C78, warning only) is skipped until the chain is cached.
- **Validation has a budget.** Market data may take `VALIDATE_BUDGET_MS` (800 ms); after that the signal becomes a review card with reason `market_data_late`. Tests and replay run with no budget.
- **The risk snapshot reads in parallel.** It quotes every open lot and reads the broker account and positions concurrently, instead of one lot at a time.
- **Quote safety:**
  - A crossed quote (bid > ask) is rejected. Validation returns review `quote_invalid`. Approval revalidation adds a `quote_valid` check that blocks the card. A locked quote (bid = ask) is allowed.
  - An Alpaca snapshot with no timestamp is stamped at the epoch, so the freshness check always rejects it. Before this it was stamped "now" and passed every staleness check.
- **Not changed:**
  - `quote_max_age_s` stays a no-default field. Unset still disables entries ("config incomplete"). The live config is 1200 s because of the indicative feed's documented 15-minute trade delay.
  - Exit pricing does not check quote age. Blocking an exit on a stale quote could leave a position stuck open.

## Where
| Area | Change |
|---|---|
| `marketdata/cached.py` (new) | `CachedMarketData`, a `MarketData` wrapper that caches the optionable list (24 h), expiries (1 h) and chains (TTL); `chain_within()` |
| `marketdata/cache.py` | `TtlCache.peek()` |
| `marketdata/freshness.py` | `is_crossed()` |
| `marketdata/alpaca.py` | no timestamp is stamped as `NO_TIMESTAMP` (the epoch) |
| `validate/service.py` | uses the wrapper; contract snapshot quote; `market_data_late`, `quote_invalid`, `quote_unavailable` |
| `decide/risk_snapshot.py` | lot marks and broker state read with `asyncio.gather` |
| `cards/approval_service.py` | `quote_valid` preflight check |
| `config.py`, `main.py` | `CHAIN_TTL_S`, `VALIDATE_BUDGET_MS`; the wrapper is wired in front of validation only (E2E fakes keep `app.state.marketdata` raw) |
| Frontend `features/cards/preflight-summary.tsx` | label for `quote_valid` |

## Tests
- `tests/unit/marketdata/test_cached_marketdata.py`: TTL, quick vs slow cold load, a single load per key, a failed load is retried.
- `tests/unit/marketdata/test_freshness.py`: crossed vs locked quotes. `test_alpaca_marketdata.py`: a missing timestamp gives the epoch.
- `tests/integration/validate/test_validation_service.py`:
  - a crossed quote leads to review;
  - the decision uses the contract quote, not the cached chain row;
  - a cold slow chain validates on the snapshot, with no adjacent warning and an unlisted strike still refused;
  - an over-budget fetch leads to `market_data_late`.
- `tests/integration/graph/test_paper_flow.py`: a quote that crosses before approval invalidates the card and places no order.

## Not measured yet
These need a paper session at market hours: live validate p50/p90 after the change, the chain cache hit rate, and how often `market_data_late` fires. The Milestone A gate in the plan covers this.
