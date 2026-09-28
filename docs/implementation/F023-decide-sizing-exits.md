# F023 — Decide: Sizing, Card Tier, Exits, TP Legs

## Objective
The pure decision engine core: given a validated signal, its link, the provider position, the user's lot and config, compute the proposed card(s) — action, quantity, limit price, bracket legs, card tier, labels — with no I/O.

## Requirements covered
- Decision log: C55.
- PRD FR-17 (actions + quantities from tiers and fractions), C15 (tier → max dollars, code computes qty), 7.1 steps 2–6, 7.3 step 3 (exit cards: fraction pre-fill rounded down; blank when not stated; soft-exit label), C51, C26, C52, C82 (0 after rounding → blank qty + "lot too small to trim: exit all or skip"), 7.4 (tier cannot buy one contract → disabled, never round up; TP split C56/C83; breakeven shows both prices C29; stop cards), 7.8 (stock sizing C76; no-price stock entry not actionable C60), §10 card tiers (normal / inferred / review / non-actionable), C31, C32, AC-14, AC-15, AC-16.
- TRD §6 (pure `decide`, sizing formula, fractional exits, TP legs 2/2/1, card tier: confidence only lowers).

## Dependencies
- Depends on: F022, F015, F006
- Produces: `decide(inputs: DecisionInputs) -> list[ProposedAction]` (first half; F024 adds risk/staleness/warnings stages).
- Used by: F024, F026, F036 (re-decide after edits), F045, F047.

## Scope
```python
class DecisionInputs(BaseModel): signal: ParsedSignal; validation: ValidationResult; link: LinkDecision
    position: ProviderPositionView | None; lot: LotView | None; config: RuntimeConfig
    quote: Quote | None; tick: Decimal | None; account_equity: Decimal | None; now: datetime; posted_at: datetime
    flags: FlagsView; risk: RiskSnapshot; age_s: float; stale: bool; late: bool     # used by F024
class ProposedAction(BaseModel): action: CardAction; card_type: CardType; qty: int | None
    limit_price: Decimal | None; stop: StopProposal | None; tp_legs: list[TpLeg]; labels: list[str]
    warnings: list[str]; missing_fields: list[str]; disabled_reason: str | None
    valid_until: datetime | None; breakeven_options: BreakevenOptions | None; suggested_underlying: str | None
```
Rules:
| Case | Output |
|---|---|
| non-actionable `msg_type` (watch, commentary_pnl, chatter, out_of_scope_strategy), `conditional` (OD-18: feed with label; C30), `tp_hit_notice` (OD-18) | `[]` (feed only) |
| option entry | price = single price or **top of range**; `limit = round_to_tick(price × (1 + entry_chase_pct))`; tier = `size_tier` or `default`; `qty = floor(tier_usd / (limit × 100))`; `qty == 0` → **disabled** "tier cannot buy one contract" |
| stock entry | price required else `[]` (C60). "X% commons" → `floor(min(X% × equity, stock_tier_max) / limit)` (**OD-26:** how the % is extracted — no Appendix A role exists yet); else `floor(stock_tier / limit)` |
| add (linked) with lot | sized like an entry (**OD-18** proposal: same tier rule), card `add` |
| add / trim / exit / stop / tp with **no lot** | `[]` — ledger only, feed (7.3 step 2; OD-18) |
| trim with fraction f and lot q | `floor(f × q)`; 0 → qty `None` + label `lot_too_small` (C82, AC-15) |
| trim `other_stated` / `not_stated` | qty `None` (C26) |
| full exit | qty = lot q |
| soft exit | qty `None` + label `suggestion` (C52) |
| stop set/move with lot | card `stop`: basis premium → broker-held stop; underlying → system-held (C49); stock → stop-market GTC (C61); breakeven → `breakeven_options = {user_avg, provider_entry}` (C29); candle close → info label |
| stop cancel with lot | card `stop` (cancel) — always needs approval (7.4) |
| tp_set with lot | card `tp` with legs |
| bracket at entry (SL/TP stated) | legs: `n = min(len(tps), qty)`; split qty over n equally, remainder from TP1 (5/3 → 2/2/1; 2 with 3 TPs → 1/1; 1 → TP1) (C83, AC-16) |
| card tier | `review` if validation `review`, link `ambiguous`/`none` on follow-up, missing required field, suggested underlying; else `inferred` if any `context` provenance; else `normal`. Model confidence can only lower (C31) |
| validity | entry/add: `valid_until = posted_at + entry_validity_s`; exits/stops/tp: `posted_at + exit_card_timeout_s` (C22, C90) |
`round_to_tick` uses the tick from `MarketData.tick_size` (**OD-25:** tick source); if tick unknown → review "tick unknown" (never assume).

## Out of scope
Risk limits, staleness, off-hours, opposite-direction warning (F024). Persistence (F026).

## Files to create
| File | Why / owns | Must NOT contain |
|---|---|---|
| `backend/app/decide/types.py` | inputs/outputs | logic |
| `backend/app/decide/sizing.py` | option/stock sizing, tick rounding | I/O |
| `backend/app/decide/exits.py` | fraction, soft exit, lot-too-small | I/O |
| `backend/app/decide/brackets.py` | TP leg split, stop proposal, breakeven options | I/O |
| `backend/app/decide/engine.py` | `decide()` composition + card tier | I/O |
| `backend/tests/scenarios/test_decide_trade_patterns.py` | TRD §12 real-trade scenario table | — |
| `backend/tests/unit/decide/test_sizing.py`, `test_exits.py`, `test_brackets.py` (+ hypothesis: legs sum = qty, legs ≤ qty, non-increasing from TP1) | tests | — |

## Tests (scenario table, TRD §12)
- "trim half" on 1-contract lot → blank qty + `lot_too_small` (AC-15).
- Entry 2 contracts with 3 TPs → TP1 1, TP2 1, no TP3 (AC-16); 5 with 3 → 2/2/1.
- Tier $100 at $2.50 premium → disabled "tier cannot buy one contract".
- Missing underlying → review with suggestion, `missing_fields=["underlying_confirmation"]` (AC-14).
- Inferred field → `inferred`; a high confidence never turns review into normal.

## Acceptance criteria
Every rule in the table has a test; `decide` is deterministic (same inputs → same outputs) and does no I/O.

## Implementation steps
1. Types. 2. Sizing + tick rounding. 3. Exits. 4. Brackets/stops. 5. Engine + tier. 6. Scenario tests.

## Open decisions touched
OD-18, OD-25, OD-26.
