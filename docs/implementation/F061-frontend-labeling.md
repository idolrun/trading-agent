# F061 — Labeling Screen

## Objective
Let the user confirm or correct LLM pre-labels quickly (classification, fields, link), producing evaluation labels for the replay gate.

## Requirements covered
- PRD C64 (LLM pre-labels; user corrects in the dashboard labeling screen), §17 (dataset for replay gate), FR-25 (labeling screen).

## Dependencies
- Depends on: F053 (backend F048)
- Produces: `/labels` route.
- Used by: F064 (replay gate data collection).

## Components
Queue list (provider filter) → label editor: message + context, pre-label shown per signal (type, instrument, fields with spans highlighted), add/remove signals (multi-signal), link target picker (provider book at that time), "correct" / "confirm" buttons (`PUT /api/v1/labels/{eventKey}`); keyboard-first (next/confirm shortcuts); progress counter (actionable vs non-actionable, providers covered) toward the PRD §17 minimums.

## Files to create
`frontend/features/labels/{types.ts,queries.ts,mutations.ts,label-queue.tsx,label-editor.tsx}`, `frontend/app/(desk)/labels/page.tsx`, tests.

## Tests
Confirm unchanged prelabel; edit a field; add second signal; link picker limited to that provider's positions.

## Acceptance criteria
A labeler can process a message with ≤ 2 keystrokes when the prelabel is right; progress toward gate minimums visible.

## Implementation steps
1. Queries/mutations. 2. Queue. 3. Editor. 4. Shortcuts + progress. 5. Tests.
