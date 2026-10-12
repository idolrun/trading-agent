# F055 — Card Inbox & Approval

## Objective
The core screen: pending cards with everything needed to decide in seconds — author, source messages, highlighted inferred fields, quantity, price, bracket legs, warnings, labels, validity countdown, advisory note — with one-tap approve (inferred cards included), qty editing, review completion, and disabled cards shown with reasons.

## Requirements covered
- PRD G3, 7.1 step 4–5 (author prominent, source messages, highlighted inferred fields, an empty required Quantity (F085/C105), bracket levels, warnings; one click incl. inferred; review cards need missing fields), 7.2 (expiry shown), 7.3 (blank qty / "Provider said half" / "suggestion, provider still holding"; full exit = Close card), 7.4 (TP split editable; breakeven choice), 7.5 (correction states), C29, C32, C57, C75 (disabled cards visible), C80 (note labeled "advisory"), FR-14/AC-14 (confirm underlying), AC-15, AC-16, §21 (one-tap approve on phone).

## Dependencies
- Depends on: F054 (backend F030, F036)
- Produces: card list + detail UI, decision mutations.
- Used by: F062.

## Routes / components
| Route | Component | Boundary |
|---|---|---|
| `/` (desk) | `CardInbox` (existing, extended) | client (live data) |
| `/cards/[cardId]` | `CardDetailScreen` (existing, extended) | client |
Components (in `features/cards/`): `CardSummary` (author, action, contract, qty × price, countdown from `validUntil`, labels/badges: inferred, review, late, stale, disabled, suggestion, lot too small), `SourceMessages` (message text with literal spans highlighted via offsets; inferred fields linked to their source message), `BracketLegs` (TP split editor enforcing Σ = qty, legs ≤ qty), `ReviewFields` (inputs for each `missingFields` item: confirm suggested underlying (explicit checkbox/confirm, never preselected-approve), choose candidate position, enter qty/expiry), `BreakevenChoice`, `AdvisoryNote` (labelled "Advisory"; hidden notes not shown), `CardActions` (approve / reject / edit).

## API integration
- `GET /api/v1/cards?state=pending` (list), `GET /api/v1/cards/{id}`.
- `POST /api/v1/cards/{id}/decision` with `version`; `edit` for review completion and non-trivial edits (card returns with new version; user then approves); `approve` may carry `qty`/`tpSplit`/`breakevenChoice`.
- 409 → toast "Card changed or expired", refetch; 422 → field errors from `message`.
- No client-side business rules: the UI only mirrors server-provided `missingFields`, `labels`, `warnings`; validation messages come from the server (X4).

## States
Loading skeleton; empty ("No cards waiting"); error with retry; per-card pending state during decision (optimistic "deciding" only); expired while open → card greys out on `card.updated` event.

## Accessibility / responsive
Approve button ≥ 44 px on phone, reachable with one thumb; countdown has `aria-live="polite"`; keyboard shortcuts on desktop (A approve / R reject on focused card) with visible hints; colour never the only signal for labels.

## Files to create / modify
Modify `frontend/features/cards/{card-inbox.tsx,card-summary.tsx,card-actions.tsx,card-detail-screen.tsx,mutations.ts,queries.ts,types.ts}`; create `frontend/features/cards/{source-messages.tsx,bracket-legs.tsx,review-fields.tsx,breakeven-choice.tsx,advisory-note.tsx,countdown.tsx}` + `*.test.tsx`.

## Tests
- Every buy/sell card starts with an empty Quantity (shares or contracts) and Approve is disabled until a whole number ≥ 1 is typed; the approval always carries it (AC-15 UI, F085).
- Suggested underlying requires explicit confirmation before approve is enabled (AC-14 UI).
- TP editor prevents legs > qty (AC-16 UI).
- Disabled card shows reason and no actions.
- Inferred fields highlighted and linked to source message.
- 409 handling refetches.

## Acceptance criteria
A pending card can be approved in one tap on a 375 px viewport; every PRD 7.1 step-4 element is visible.

## Implementation steps
1. Extend types/queries for detail shape. 2. Summary + labels + countdown. 3. Source messages with highlights. 4. Review fields + breakeven + TP editor. 5. Actions with version + error handling. 6. Tests.
