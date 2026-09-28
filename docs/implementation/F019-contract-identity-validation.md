# F019 — Contract Identity Validation & Missing-Underlying Suggestion

## Objective
Prove the contract a signal names really exists and that its price is plausible; warn (never correct) when an adjacent strike fits better; and turn a missing underlying into a review card with an optional live-price-proximity suggestion.

## Requirements covered
- Decision log: C81.
- PRD FR-11 (contract exists in live chain; price within wide configurable tolerance; else review), C86 (no ticker whitelist: optionable list + live chain), FR-13 (adjacent-strike warning only), FR-14 + C79 + C84 (missing underlying → review card; suggestion by live-price proximity; never approvable without confirmation), AC-14, AC-17.
- TRD §6 missing-underlying algorithm (±P%, zero or several matches → no suggestion), §12 scenario rows for "1000c".

## Dependencies
- Depends on: F018
- Produces: `ValidationService.validate(signal, envelope, now) -> ValidationResult`.
- Used by: F023 (card tier/warnings), F035 (`validate` node), F038, F039 (revalidation at release), F041 (revalidate).

## Scope
```python
class ValidationResult(BaseModel):
    status: Literal["ok","review","not_actionable"]; reasons: list[str]; warnings: list[str]
    normalized: NormalizedSignal | None; contract: OptionContract | Stock | None
    quote: Quote | None; suggested_underlying: str | None
```
Checks (options; entries/adds and any card needing a contract):
1. Normalize (F018).
2. Underlying missing → `review`, `reasons=["underlying_missing"]`. Suggestion: underlyings this provider traded in the last `missing_underlying_lookback_days` (`provider_positions` query), fetch `underlying_price`, keep those with `|strike − price| / price ≤ proximity_pct`; exactly one → `suggested_underlying`, else none. **Always review** (C79, AC-14).
3. Underlying ∉ `optionable_symbols()` → `review` (`not_optionable`).
4. Expiry ∉ `expiries(underlying)` or strike/right ∉ `chain(...)` → `review` (`contract_not_in_chain`).
5. Price vs live mid beyond `identity_price_tolerance_pct` → `review` (`price_off_quote`).
6. Adjacent strike: if an adjacent listed strike's mid is closer to the signal price by factor `adjacent_strike_ratio` → **warning** only (`adjacent_strike_better_match`), fields unchanged.
- Stocks: symbol must be listed with a quote; stock entries without price → `not_actionable` (C60).
- Exits/stops on an existing linked position skip chain checks (contract comes from the position).
- **Market data unavailable (OD-12 proposal):** `review` with reason `market_data_unavailable` + `market_data` warning alert; checks are never skipped.

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/validate/identity.py` | pure checks over fetched chain/quote data | I/O |
| `backend/app/validate/underlying_suggest.py` | pure proximity rule | I/O |
| `backend/app/validate/service.py` | fetch market data (cached), call pure checks, recent-underlyings query | ledger writes |
| `backend/tests/unit/validate/test_identity.py`, `test_underlying_suggest.py` | TRD §12 scenarios | — |
| `backend/tests/integration/validate/test_validation_service.py` | with `FakeMarketData` | — |

## Tests
- `"spx 7775c 0dte"` → SPXW contract, today's expiry, ok (AC-17).
- `"1000c"`, provider recently traded MU near $1,000 → review + suggestion MU (approval blocked until confirmed: F023/F036).
- `"1000c"`, two underlyings within ±10% → review, no suggestion.
- Ticker outside the top 15 but optionable and in chain → ok (C86).
- Price 5× off quote → review; adjacent strike better → warning only.
- Market data raising → review + alert.

## Acceptance criteria
AC-14 and AC-17 proven at validation level; no check ever modifies a field value.

## Implementation steps
1. Result model. 2. Pure identity checks. 3. Proximity suggestion. 4. Service with cache + provider history query. 5. Tests.

## Open decisions touched
OD-12.
