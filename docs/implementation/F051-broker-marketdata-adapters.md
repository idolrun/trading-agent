# F051 — Alpaca Broker & Market-Data Adapters (built 2026-10-02; paper contract suite not yet run)

## Objective
Implement `BrokerClient`, `BrokerEvents` and `MarketData` for Alpaca (chosen 2026-10-02, OD-34). The paper account is the paper stage (PRD §17).

## Requirements covered
- Decision log: C12.
- PRD §15 broker must-haves (options, option stop orders, client order IDs + status query, paper account), market data (chains, quotes, underlying prices), account type / PDT (OD-3), §17 paper stage (OD-16), C86 optionable list.
- TRD §7.2, §7.3.

## Dependencies
- Depends on: F010, F040
- Produces: `app/alpaca_api.py`, `app/broker/alpaca.py`, `app/marketdata/alpaca.py`, settings.
- Used by: paper + live stages (F064).

## What was built
- `app/alpaca_api.py`: shared REST client (trading + data hosts, key headers). Failures are classified by whether the request can have taken effect: `AlpacaNotSent` (connect failure, 401, 429, a 403 that is not "insufficient" → `BrokerUnavailable`), `AlpacaRefused` (other 4xx → `BrokerRejected`), `AlpacaUnknown` (timeout, 5xx → `BrokerTimeout`). Option symbols are the unpadded OCC form.
- `app/broker/alpaca.py` `AlpacaBroker`: `client_order_id` is Alpaca's own unique field; a retried id (422) returns the existing order, like `FakeBroker`; lookup by `orders:by_client_order_id`. Options are long-only (BUY = `buy_to_open`, SELL = `sell_to_close`); `STOP` and `STOP_MARKET` both send `stop`. `events()` listens on the `trade_updates` WebSocket with reconnect and backoff; fills carry Alpaca's `execution_id` as `fill_id`. Nothing is replayed after a drop: startup reconciliation (F046) catches up. `submit_oco` refuses: legs are system-linked (OD-30).
- `app/marketdata/alpaca.py` `AlpacaMarketData`: snapshots for quotes (stocks `iex`, options `indicative` by default); quote `at` is Alpaca's timestamp so staleness checks see the delay; a zero bid or ask means no quote; contracts endpoint for expiries; options snapshots for chains; `/v2/assets?attributes=options_enabled` for the optionable list (C86); `stream()` polls snapshots (the only streaming consumer is the underlying-stop watch); ticks use the coarse grid (0.05 below $3, 0.10 above, valid in every class; per-root refinement is OD-25).
- Settings: `BROKER_ADAPTER` / `MARKETDATA_ADAPTER` = `fake` | `alpaca`, `ALPACA_ENDPOINT` (paper or live host, the `/v2` suffix is optional; the live host only with `ENVIRONMENT=production`), `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_STOCK_FEED`, `ALPACA_OPTIONS_FEED`, `ALPACA_POLL_INTERVAL_S`. One key pair per process.

## Accepted gap
Alpaca lists US equity and ETF options only: no SPX index quote, no SPXW chain. SPXW signals (C85) become review cards (`not_optionable` / market-data unavailable). Confirmed by the user 2026-10-02; the live suite records it.

## Still to verify on the paper account (`uv run pytest -m live tests/live/test_alpaca.py`, needs paper keys)
The docs were read through summaries and no call has been made from this code. The unit tests use canned responses, so these assumptions are unproven until the live suite runs:
- [ ] paper account has options enabled (level 2: buying calls/puts); option stop orders and GTC are accepted; `position_intent` values
- [ ] `fill` / `partial_fill` `qty` is the size of that fill (not cumulative); `execution_id` unique per fill
- [ ] WebSocket frames (text vs binary), authorization message shape, the `new` update for a fresh order
- [ ] 403 messages: "insufficient buying power" vs bad credentials (the classification keys on that word)
- [ ] snapshot endpoints and parameters (`expiration_date`, paging, unknown symbols → 400 or omitted)
- [ ] free-feed quality: IEX stock quotes can be thin or zero; indicative option quotes and their delay
- [ ] rate limits against 5 s underlying polling; paper partial fills (docs: about 10% random)
- [ ] account type / PDT (OD-3)

## Files
| File | Change |
|---|---|
| `backend/app/alpaca_api.py` | REST client, symbol and decimal helpers |
| `backend/app/broker/alpaca.py` | `BrokerClient` + `BrokerEvents` |
| `backend/app/marketdata/alpaca.py` | `MarketData` incl. stream |
| `backend/app/config.py`, `backend/app/main.py`, `.env.example`, `backend/pyproject.toml` | adapter selection, secrets, explicit `httpx` + `websockets` dependencies |
| `backend/tests/unit/broker/test_alpaca_broker.py`, `tests/unit/marketdata/test_alpaca_marketdata.py`, `tests/unit/test_config_alpaca.py` | adapter behaviour against canned responses and a fake socket |
| `backend/tests/live/test_alpaca.py` (marker `live`, paper only) | account, quotes, SPX gap, chain, order submit/duplicate/lookup/cancel, stream |

## Security
Credentials only in secret env; never logged; the secret leaves `AlpacaApi` only inside the WebSocket auth message; paper and live keys are never both loaded in one process; the live `ALPACA_ENDPOINT` is refused outside `ENVIRONMENT=production`, and any host other than Alpaca's two is refused.

## Acceptance criteria
All live contract tests pass on the paper account; fakes and the real adapter pass the same port-level behaviour.
