# F056 — Message Feed

## Objective
A live feed of every message event with its classification label and processing status, so nothing is ever hidden (non-actionable, stale, late, backfilled, muted, failed).

## Requirements covered
- PRD FR-10 (non-actionable in feed with classification label), C33, FR-1c ("stale" label), FR-1b ("late"), FR-1d (backfilled), FR-1e (failed visible), FR-2/C57 (author shown; muted), 7.3 step 2 (feed-only follow-ups), G5.

## Dependencies
- Depends on: F054 (backend F030 `/feed`)
- Produces: `/feed` route.
- Used by: F062.

## Route / components
`app/(desk)/feed/page.tsx` → client `FeedScreen` (`features/feed/`): filter by provider and label, infinite list (cursor), `FeedItem` (author, local time, text, badges, msg types, link to cards). Muted authors are shown with a `muted` badge; muting/unmuting is done on the config screen (F060), which may later add a shortcut here.

## States
Loading, empty, error, "new messages" pill when scrolled away and SSE `feed.message` arrives.

## Accessibility / responsive
List semantics, badges with text, time shown in the user's local zone (UTC+5:45) with ET on hover/tooltip.

## Files to create
`frontend/features/feed/{types.ts,queries.ts,feed-screen.tsx,feed-item.tsx}` + tests; `frontend/app/(desk)/feed/page.tsx`; nav link in desk layout.

## Tests
Badges for each label; pagination; SSE-triggered refresh.

## Acceptance criteria
Every inbox status/label is visibly rendered; nothing filtered by default.

## Implementation steps
1. Types/queries. 2. Screen + item. 3. Filters + pagination. 4. Tests.
