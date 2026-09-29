// Web Push (F054). Payload from backend/app/notify/push.py: {"title": str, "url": "/cards/<id>"}.

self.addEventListener("install", () => self.skipWaiting())
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()))

/** Same-origin paths only: the payload is never trusted to send the user elsewhere. */
function safePath(url) {
  try {
    const target = new URL(url, self.location.origin)
    return target.origin === self.location.origin ? target.pathname + target.search + target.hash : "/"
  } catch {
    return "/"
  }
}

self.addEventListener("push", (event) => {
  let payload = {}
  try {
    payload = event.data ? event.data.json() : {}
  } catch {
    // not JSON: show the generic notification
  }
  const title = typeof payload.title === "string" && payload.title ? payload.title : "Trading desk"
  event.waitUntil(
    self.registration.showNotification(title, {
      icon: "/icon-192.png",
      data: { url: safePath(payload.url) },
    }),
  )
})

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const url = safePath(event.notification.data && event.notification.data.url)
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true })
      const existing = windows.find((client) => client.url.startsWith(self.location.origin))
      if (existing) {
        try {
          await existing.focus()
          await existing.navigate(url)
          return
        } catch {
          // an uncontrolled client cannot be navigated: open a new window instead
        }
      }
      await self.clients.openWindow(url)
    })(),
  )
})
