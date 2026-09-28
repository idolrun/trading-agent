# F018 — Normalization: Symbol Map, Dates, Price Scale

## Objective
Turn interpreted raw values into broker-ready values by code only: map provider shorthand to broker symbols, resolve relative expiries, and pick the price scale that matches the live quote — never altering a number's digits.

## Requirements covered
- PRD FR-12 (price notation normalized by the scale matching the live quote; "140/con" = $1.40), FR-15 (relative dates by code against the ET exchange calendar), FR-15a + C85 (symbol map, default SPX → SPXW), C78 (numbers never auto-corrected), AC-17 (SPX resolves to SPXW).
- TRD §6 symbol map, §16.2 #2, #5.

## Dependencies
- Depends on: F007, F010, F015
- Produces: `normalize(signal: ParsedSignal, *, now, config, marketdata, profile_aliases) -> NormalizedSignal | NeedsReview`.
- Used by: F019, F023, F035.

## Scope
1. **Symbol:** uppercase, strip `$`; apply `config.symbol_map` then user-approved provider aliases (F047 writes them); provenance becomes `resolved(rule="symbol_map", inputs={raw})`.
2. **Expiry:** `markettime.resolve_expiry_token(token, now)`; unresolvable → `NeedsReview(reason="expiry_unresolved")`; missing expiry on an option entry → `NeedsReview(missing=["expiry"])` (no default expiry is ever assumed).
3. **Price scale (options):** candidates `{v, v/100}`; `price_unit_hint="per_contract"` forces `v/100`. Otherwise choose the candidate whose ratio to the live mid is closest to 1, and require it within `identity_price_tolerance_pct`; if neither is within tolerance or both are equally plausible → `NeedsReview(reason="price_scale_ambiguous")`. Stocks: no scaling.
4. Output keeps both raw and normalized values for the card ("source text 140/con → $1.40").

## Out of scope
Chain existence, adjacent strike, missing-underlying suggestion (F019).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/validate/normalize.py` | pure functions + `NormalizedSignal` | network calls (quotes passed in by the caller) |
| `backend/tests/unit/validate/test_normalize.py` | tests | — |

Design note: the pure core takes a `quotes` mapping; a thin async wrapper in F019's service fetches quotes first (so validation stays testable and no transaction is open during I/O).

## Tests (unit)
- `spx` → `SPXW`; `$tsla` → `TSLA`; unknown symbol unchanged (validity is F019).
- `0dte` posted 10:04 ET Monday → Monday; `friday` Friday → Friday (OD-22 proposal).
- `140/con` → 1.40; `260` with mid 2.55 → 2.60; `260` with mid 255 (stock-like option) → 260; `3.2` with mid 3.15 → 3.2; `.95` → 0.95; ambiguous → review.
- Digits never change (property: normalized value ∈ {v, v/100}).

## Acceptance criteria
AC-17 symbol part; FR-12 examples pass; no digit changes.

## Implementation steps
1. Symbol mapping. 2. Expiry resolution + review reasons. 3. Scale selection. 4. Tests.

## Open decisions touched
OD-22.
