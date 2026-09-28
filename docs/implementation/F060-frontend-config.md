# F060 — Config Screen

## Objective
View and edit the runtime configuration (tiers, X/N/M, re-price, cutoffs, risk limits, feed settings, channels, muted authors, symbol map, thresholds, LLM chain) with server-side validation, version conflict handling and a change history.

## Requirements covered
- PRD §18 configuration list, FR-25 (config view), C57 (mute authors), C69, PRD (e) (unset money values shown as "not set — entries disabled").

## Dependencies
- Depends on: F053 (backend F006, F031 validation)
- Produces: `/config` route.
- Used by: F062.

## Components
Grouped form sections mirroring `RuntimeConfig` groups; "missing for entries" summary at top (from `missingForEntries`); save sends full document + `expectedVersion` + reason; 409 → reload + diff prompt; 422 → field errors mapped from server; history list from `config_changes` (add `GET /api/v1/config/changes` to F006 if not present — update F006 doc when implementing). No client-side business validation beyond input types.

## Files to create
`frontend/features/config/{types.ts,queries.ts,mutations.ts,config-screen.tsx,sections/*.tsx}`, `frontend/app/(desk)/config/page.tsx`, tests.

## Tests
Unset money fields highlighted; conflict flow; server errors displayed per field.

## Acceptance criteria
Every PRD §18 value editable; every save audited server-side with reason.

## Implementation steps
1. Types/queries. 2. Sections. 3. Save + conflict. 4. History. 5. Tests.
