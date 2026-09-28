# F005 — Schema: Trading State & Trading Audit

## Objective
Create provider positions, lots, cards, approvals, orders, fills, stops, timers and their history tables per [`database-schema.md` §4](database-schema.md#4-trading-state-and-trading-audit-tables-f005), including the constraints that make key safety properties impossible to violate at the database level.

## Requirements covered
- PRD FR-16 (two ledgers, lot per provider), C24, C47, C62, FR-20(1) shape, FR-22 (client order IDs), AC-2 (order needs approval or allowed auto reason), §14 (cards + transitions, approvals, orders + state history, fills, stops/TPs, provider positions + history), C93 (RPO≈0 set).
- TRD §6 state machines, §8, §8.1, OD-13 (proposed `*_events` history tables).

## Dependencies
- Depends on: F004
- Produces: tables `provider_positions`, `provider_position_events`, `lots`, `lot_events`, `cards`, `card_events`, `approvals`, `orders`, `order_events`, `fills`, `stops`, `stop_events`, `timers`; composite FKs on `message_links`; FK `model_calls.card_id`; migration `0004_trading_state`.
- Used by: F017, F020–F028, F036–F047.

## Scope — safety constraints that must exist
| Constraint | Protects |
|---|---|
| `message_links (provider_position_id, provider_id) → provider_positions (provider_position_id, provider_id)` and `(signal_id, provider_id) → signals` | Links never cross providers (PRD §6, AC-18) |
| `lots` partial UNIQUE `(provider_position_id) WHERE status='open'` | One open lot per provider position (C24); re-entry = new lot (C62) |
| `lots` CHECK `is_foreign = (provider_position_id IS NULL)` | Foreign lots (C47) |
| `cards` UNIQUE `(signal_id, action)` and UNIQUE `dedupe_key` | No duplicate card on re-processing (TRD §9) |
| `cards` CHECK `(card_type='disabled') = (state='disabled')` | X3 |
| `approvals` UNIQUE `card_id` | A card is decided once |
| `orders` CHECK `approval_id IS NOT NULL OR auto_reason IS NOT NULL`; `auto_reason` ∈ PRD §9 list | AC-2 |
| `orders` CHECK `(kind='entry') = (side='buy')` | FR-20(1) shape |
| `orders` UNIQUE `client_order_id`, UNIQUE `broker_order_id` | FR-22, AC-7 |
| `fills` UNIQUE `broker_fill_id` | duplicate fill events |
| `timers` UNIQUE `dedupe_key` | duplicate deadlines |

## Out of scope
Ledger/card/execution logic (F020+).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/ledger/models.py` | `ProviderPosition`, `ProviderPositionEvent`, `Lot`, `LotEvent`, enums | ledger rules |
| `backend/app/cards/models.py` | `Card`, `CardEvent`, `Approval`, `CardState`, `CardType`, `CardAction`, `CardSource` | state machine (F025) |
| `backend/app/execution/models.py` | `Order`, `OrderEvent`, `Fill`, `Stop`, `StopEvent`, enums | pricing / invariants |
| `backend/app/timers/models.py` | `Timer`, `TimerKind`, `TimerStatus` | scheduler |
| `backend/migrations/versions/0004_trading_state.py` | DDL, composite FKs, grants | data |
| `backend/tests/integration/schema/test_trading_constraints.py` | tests | — |

## Files to modify
`backend/app/db_models.py`; `backend/app/interpret/models.py` (declare composite FKs on `MessageLink`).

## Tests (integration)
- Cross-provider link insert (signal of provider A → position of provider B) → FK violation.
- Second open lot for the same provider position → unique violation; open lot after the first is closed → allowed.
- Order with neither approval nor auto reason → violation; `auto_reason='other'` → violation; `kind='exit', side='buy'` → violation.
- Duplicate `(signal_id, action)` card → violation.
- Second approval row for one card → violation.
- App role cannot UPDATE `approvals`, `fills`, `card_events`, `order_events`.

## Acceptance criteria
All constraint tests pass; schema matches doc §4; up/down/up clean.

## Implementation steps
1. Models + enums. 2. Autogenerate `0004`; add composite FKs on `message_links`, partial unique index, CHECKs, grants, `model_calls.card_id` FK. 3. Constraint tests. 4. Up/down/up.

## Definition of done
Global DoD. **Milestone M1** reached (README §6).
