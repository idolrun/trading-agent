# F066 — Alpaca-paper lifecycle hardening

## Objective
Make `signal → AI → market + account → risk → card → approve → revalidate → Alpaca paper order → fill → lot → reconcile` correct for the Alpaca **paper** account, without changing the architecture: models interpret, code decides, the human approves, `submit_order()` is the only order path.

## What changed (gap → implementation)
| Gap found by the audit | Implementation |
|---|---|
| Jev off by default | `JevConfig` default `enabled=True`, `model="jev-latest"`; migration `0017` flips existing config rows (audited, idempotent). |
| Laya only when Jev gave *no* answer, off by default | `LayaConfig.enabled=True`. Message graph: any Jev `slow_path` (no answer, low confidence, several signals, missing fields) → `laya_interpret` → optional LLM → review. Jev `assemble`/`feed_only` never calls Laya. Laya keeps its strict gate. |
| Broker account never read | `BrokerAccount` gains `cash`, `options_buying_power`. `build_risk_snapshot(broker=…)` reads account + positions fresh; `entry_block_reason` adds "insufficient buying power" and "risk unknown: account unavailable" (fail closed). Account data never reaches Jev/Laya. |
| Working orders invisible to risk | `execution.repo.working_entries`; exposure and open positions include unfilled entry orders; a holding at the broker that no lot shows counts at the broker's cost. |
| Paper/live mismatch possible | `broker/safety.py:check_mode` enforced at startup, on `PUT /config` / config CLI (`ConfigService(extra_errors=…)`) and in `OrderService._submit` (`ModeMismatch`, nothing stored or sent). `paper` ⇒ `alpaca-paper`; `live` ⇒ `alpaca-live`; the fake broker serves paper/live only when `ENVIRONMENT=test`. |
| `Quote.at` never checked | `quote_max_age_s` (no default → entries stay "config incomplete" until set). `marketdata/freshness.py`; applied in validation, follow-up scaling, risk marks, revalidation and exit pricing. Alpaca's free options feed is ~15 min delayed: set ≈ 1200. |
| Revalidation thin; no supersession after approval | `ApprovalService._preflight` runs hours, validity, quote, freshness, price band, supersession (`ledger.repo.superseded_after_approval`), account, working orders, risk, exit lot + broker position. `revalidate()` fails on the first non-pass check. |
| Broker rejection left the card `submitted` | New `CardReason.ORDER_REJECTED` (migration `0018`): approved → invalidated when every order of the card is rejected. |
| Reconciliation only compared quantity | `compare` also flags average-cost drift on tracked lots; account (equity, cash, buying power) and position count are reported on the health view, never gating. |
| Card did not show what the decision rests on | `GET /api/v1/cards/{id}/preflight` (read-only) + `PreflightSummary`: PASS / REVIEW / BLOCKED per check, bid/ask, proposed price, estimated cost, buying power before → after, exposure before → after (limit), equity, cash, daily P&L. |

## Paper setup
```
BROKER_ADAPTER=alpaca  MARKETDATA_ADAPTER=alpaca
APCA_API_BASE_URL=https://paper-api.alpaca.markets   # api.alpaca.markets only with ENVIRONMENT=production
APCA_API_KEY=… APCA_SECRET_KEY=…
TYPESAFE_API_KEY=…                                   # Jev
```
Config page: `trading_mode=paper`, `quote_max_age_s` (≈1200 on the free indicative options feed), plus the existing tiers and limits. Existing databases: `uv run alembic upgrade head` (0017 switches Jev and Laya on).

## Tests
- `tests/integration/graph/test_paper_flow.py`: Jev ok (Laya not called), Jev down → Laya, Jev unsure → Laya, neither → review/no order, approve → order → fill → lot → reconcile, preflight numbers, buying power (at card and at approval), unreadable account, working order over the exposure limit, stale quote (card and approval), supersession after approval, submit timeout (query, no resend), broker rejection, mode mismatch, partial + duplicate fill, no account data in model requests.
- Unit: `test_freshness`, `test_safety`, reconcile average-cost rules, risk gates; integration: risk snapshot (account, working entries, broker-only holding, stale mark), config extra errors, preflight API.
- `tests/live/test_paper_app_flow.py` (opt-in, `-m live`, market hours): the same path against the real Alpaca paper account with the real quote, order stream and reconciliation. Interpretation is recorded there.

## Still open
PDT day-trade counting; `% of equity` stock sizing (PRD C76); native OCO; SPX/SPXW on Alpaca; a card transition for a broker rejection that arrives *after* acceptance (the order row and the card UI show the order state); Jev/Laya accuracy on real signals (bench not run).
