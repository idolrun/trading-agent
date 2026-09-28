# F051 — Real Broker & Market-Data Adapters (**blocked on S2/S3 / OD-3, OD-4, OD-16**)

## Objective
Implement `BrokerClient`, `BrokerEvents` and `MarketData` for the chosen broker and data source (TRD recommendation: spike Tradier first, IBKR fallback), including SPX index data and paper-account configuration.

## Requirements covered
- Decision log: C12.
- PRD §15 broker must-haves (options incl. SPX/SPXW, stop orders on options, OCO/brackets on options, GTC stock stop-market, client order IDs + status query, paper account), market data (chains, quotes, underlying prices incl. SPX index), account type / PDT (OD-3), §17 paper stage (OD-16), C86 optionable list.
- TRD §7.2, §7.3.

## Dependencies
- Depends on: F010, F040 (and spike S2/S3 answers)
- Produces: `broker/<name>.py`, `marketdata/<name>.py`, settings for credentials (secrets), `Settings.broker_adapter` new value.
- Used by: paper + live stages (F064).

## Unblock checklist
- [ ] Broker chosen; account type decided (PDT counter needed? → add `pdt` risk gate to F024 + config).
- [ ] Capability matrix verified in sandbox: SPX options, option stop orders, OCO stop+multi-TP semantics (drives OD-30), GTC stock stop-market, client order ID field + lookup, duplicate client ID behaviour, order replace support.
- [ ] Event transport (stream vs poll) → OD-10.
- [ ] Market data: real-time SPX index entitlement, option chain + quotes latency, optionable symbol list endpoint, tick size rules (OD-25).
- [ ] Paper-stage approach if live broker has no paper (OD-16).

## Scope
Adapters only; no change to execution policies except capability-driven branches already abstracted in F042 (native OCO vs system-managed).

## Files to create / modify
| File | Change |
|---|---|
| `backend/app/broker/<name>.py` | `BrokerClient` + `BrokerEvents` |
| `backend/app/marketdata/<name>.py` | `MarketData` incl. stream |
| `backend/app/config.py` | adapter selection + secrets |
| `backend/tests/contract/test_broker_<name>.py` (marker `live`, sandbox) | submit/cancel/status/OCO/stop/fill events |
| `backend/tests/contract/test_marketdata_<name>.py` (marker `live`) | chain, quote, SPX, stream |

## Security
Credentials only in secret env; never logged; paper and live credentials never both loaded in one process.

## Tests
Sandbox contract suite covering every order path PRD §17 stage 3 lists (entry, bracket, trim, add, broker stop, underlying stop, stop restore, EOD forced close, crash recovery).

## Acceptance criteria
All contract tests pass in sandbox/paper; fakes and real adapter pass the same port-level test suite.

## Implementation steps
1. Unblock checklist. 2. Market data adapter. 3. Broker adapter. 4. Event transport. 5. Contract suites. 6. Update README decisions.
