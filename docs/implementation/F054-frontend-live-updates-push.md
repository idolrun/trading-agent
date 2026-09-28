# F054 — Live Updates & Push (SSE, service worker, PWA)

## Objective
Keep every open screen current via SSE-driven TanStack Query invalidation, and deliver Web Push notifications to desktop and phone through an installable PWA.

## Requirements covered
- PRD FR-25/FR-26 (push for cards, fills, automatic actions, alerts, risk limits), C21, C58, §12 (card displayed on an open dashboard within 5 s p95), TRD §3 (iOS requires PWA on Home Screen).

## Dependencies
- Depends on: F053 (backend F029)
- Produces: `useLiveEvents()` (mounted once in the desk layout), `public/sw.js`, `app/manifest.ts`, push subscription toggle.
- Used by: F055–F058.

## Scope
- `EventSource("/api/v1/events", { withCredentials: true })`; map event types → `queryClient.invalidateQueries` on the matching `domainKeys` (e.g. `card.created` → `cardKeys.lists()`; `card.updated` → list + detail). Reconnect with backoff; on reconnect invalidate everything (missed events).
- Connection indicator in the header (live / reconnecting).
- **Verify SSE through `rewrites`** is not buffered (R3 in README): measure event latency in dev and prod-like build; if buffered, fall back to the documented alternative (API on a same-site subdomain) and record it.
- Push: `navigator.serviceWorker.register("/sw.js")`; "Enable notifications" button (user gesture) → `Notification.requestPermission()` → `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidPublicKey })` → `POST /api/v1/push/subscriptions`. `sw.js` shows notifications and focuses/opens `/cards/{id}` on click.
- `app/manifest.ts` (name, icons, `display: "standalone"`, start URL) so iOS users can add to Home Screen; in-app hint on iOS Safari when not installed.

## Files to create
`frontend/features/live/{use-live-events.ts,event-map.ts,connection-indicator.tsx}`, `frontend/features/push/{use-push-subscription.ts,push-toggle.tsx}`, `frontend/public/sw.js`, `frontend/app/manifest.ts`, icons in `frontend/public/`, tests.

## Files to modify
`frontend/app/(desk)/layout.tsx` (mount live events + indicator).

## Tests
- Event map: each event type invalidates the right keys (unit).
- Reconnect invalidates all (unit with a fake EventSource).
- Push toggle flow with mocked browser APIs.

## Acceptance criteria
A card created in the backend appears on an open dashboard without manual refresh; push subscription stored; notification click opens the card.

## Implementation steps
1. Event map + hook. 2. Indicator. 3. SSE latency verification. 4. Service worker + manifest. 5. Push toggle. 6. Tests.
