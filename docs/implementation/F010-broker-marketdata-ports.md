# F010 — Broker & Market-Data Ports + Fakes

## Objective
Define the only interfaces through which the system talks to a broker and to market data, plus deterministic in-memory fakes. The whole pipeline, execution logic and shadow mode run on these fakes until the broker (OD-3) and data source (OD-4) are chosen (F051).

## Requirements covered
- PRD §15 broker capabilities (options incl. SPX, option stops, OCO/brackets, GTC stock stop-market, client order IDs + status query, paper), market data (chains, quotes, underlying incl. SPX index), C86 (optionable symbols list).
- TRD §2.2 (`broker`, `marketdata` thin adapters), §7.2, §7.3, §15.2 (in-process caches behind accessors), coding-practices §22.

## Dependencies
- Depends on: F001
- Produces: `app/instruments.py` value types, `BrokerClient` and `BrokerEvents` Protocols, `MarketData` Protocol, `FakeBroker`, `FakeMarketData`, `QuoteCache` (in-process TTL).
- Used by: F018, F019, F028, F040–F047, F051, tests everywhere.

## Scope
```python
# app/instruments.py
class OptionContract(BaseModel, frozen=True): underlying: str; expiry: date; strike: Decimal; right: Literal["call","put"]
    def occ_symbol(self) -> str
class Stock(BaseModel, frozen=True): symbol: str
Instrument = OptionContract | Stock

# app/marketdata/port.py
class Quote: bid: Decimal | None; ask: Decimal | None; last: Decimal | None; at: datetime
class MarketData(Protocol):
    async def quote(self, instrument: Instrument) -> Quote
    async def underlying_price(self, symbol: str) -> Quote          # includes SPX index level
    async def expiries(self, underlying: str) -> list[date]
    async def chain(self, underlying: str, expiry: date) -> list[tuple[OptionContract, Quote]]
    async def optionable_symbols(self) -> frozenset[str]            # refreshed daily (TRD §7.3)
    async def tick_size(self, instrument: Instrument, price: Decimal) -> Decimal   # price rounding (OD-25)
    def stream(self, symbols: Iterable[str]) -> AsyncIterator[tuple[str, Quote]]

# app/broker/port.py
class OrderRequest: client_order_id: str; instrument: Instrument; side; qty; order_type; limit_price; stop_price; tif
class OcoRequest: client_order_id_prefix: str; legs: list[OrderRequest]     # stop + TPs one-cancels-other
class BrokerOrder: broker_order_id; client_order_id; status; filled_qty; avg_fill_price
class BrokerEvent: kind: Literal["status","fill"]; broker_order_id; client_order_id; status; fill_id; qty; price; at
class BrokerClient(Protocol):
    async def submit(self, req: OrderRequest) -> BrokerOrder          # raises BrokerTimeout / BrokerRejected / BrokerUnavailable
    async def submit_oco(self, req: OcoRequest) -> list[BrokerOrder]
    async def get_by_client_id(self, client_order_id: str) -> BrokerOrder | None
    async def cancel(self, broker_order_id: str) -> None
    async def open_orders(self) -> list[BrokerOrder]
    async def positions(self) -> list[BrokerPosition]
    async def account(self) -> BrokerAccount                          # equity, buying power (C76)
class BrokerEvents(Protocol):                                         # OD-10: stream or poll, adapter decides
    def events(self) -> AsyncIterator[BrokerEvent]
```
- Errors: `BrokerTimeout` (outcome unknown → caller must query), `BrokerRejected(reason)`, `BrokerUnavailable` (definitely not sent).
- `FakeBroker`: scriptable per `client_order_id` or default policy — `fill_now`, `partial(n)`, `never_fill`, `reject`, `timeout_then_exists`, `timeout_then_missing`; OCO semantics (one fill cancels siblings); emits `BrokerEvent`s; records every call for assertions; idempotent on duplicate `client_order_id` (returns existing — mirrors real brokers' duplicate rejection).
- `FakeMarketData`: loaded from fixture dicts (`tests/fixtures/market/*.json`), settable quotes, scripted stream.
- `QuoteCache`: TTL cache for chains/optionable list (in-process, TRD §15.2).

## Out of scope
Real adapters (F051). Execution policy (F028+).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/instruments.py` | contract/stock value types, OCC symbol | pricing |
| `backend/app/marketdata/port.py`, `fake.py`, `cache.py` | protocol, fake, TTL cache | business rules |
| `backend/app/broker/port.py`, `errors.py`, `fake.py` | protocol, errors, fake | invariants (F028) |
| `backend/tests/fixtures/market/` | chains/quotes for SPY, SPXW, QQQ, TSLA, GOOGL, META, MU | real account data (C87) |
| `backend/tests/unit/broker/test_fake_broker.py`, `backend/tests/unit/test_instruments.py` | tests | — |

## Files to modify
`backend/app/main.py` — provide adapters via lifespan-created singletons (`app.state.broker`, `app.state.marketdata`), selected by `Settings.broker_adapter = "fake"` (only value until F051).

## Security
Fixtures contain synthetic data only; never data from `docs/data/` (C87).

## Tests (unit)
OCC symbol vectors (incl. SPXW, strikes with decimals 357.5); fake OCO cancels siblings; duplicate client id returns same order; timeout scripts behave as specified.

## Acceptance criteria
Ports cover every capability listed in PRD §15 needed by F028–F047; fakes are deterministic and scriptable.

## Implementation steps
1. `instruments.py`. 2. Market-data port + fake + cache. 3. Broker port + errors + fake. 4. Fixtures. 5. Lifespan wiring. 6. Tests.

## Open decisions touched
OD-3, OD-4, OD-10.
