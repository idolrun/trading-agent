"use client"

import { useCallback, useEffect, useState } from "react"
import { apiDelete, apiGet, apiPost } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"

export type PushState = "checking" | "unsupported" | "denied" | "disabled" | "enabled"

/** `applicationServerKey` wants bytes; the backend sends the VAPID key as base64url. */
function keyToBytes(base64Url: string): Uint8Array<ArrayBuffer> {
  const base64 = (base64Url + "=".repeat((4 - (base64Url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/")
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))
}

const isSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window

/** POST is idempotent on the endpoint, so it also recovers the id needed to revoke it. */
async function register(subscription: PushSubscription): Promise<string> {
  const { endpoint, keys } = subscription.toJSON()
  const body: Schemas["SubscribeRequest"] = { endpoint: endpoint!, keys: { p256Dh: keys!.p256dh, auth: keys!.auth } }
  const created = await apiPost<Schemas["SubscribeResponse"]>("/api/v1/push/subscriptions", body)
  return created.subscriptionId
}

function initialState(subscription: PushSubscription | null): PushState {
  if (Notification.permission === "denied") return "denied"
  return subscription && Notification.permission === "granted" ? "enabled" : "disabled"
}

export function usePushSubscription() {
  const [state, setState] = useState<PushState>("checking")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const initial: Promise<PushState> = isSupported()
      ? navigator.serviceWorker
          .register("/sw.js", { scope: "/", updateViaCache: "none" })
          .then((registration) => registration.pushManager.getSubscription())
          .then(initialState)
      : Promise.resolve("unsupported")
    initial.catch((): PushState => "unsupported").then((next) => !cancelled && setState(next))
    return () => {
      cancelled = true
    }
  }, [])

  const run = useCallback(async (action: () => Promise<PushState>) => {
    setBusy(true)
    setError(null)
    try {
      setState(await action())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong")
    } finally {
      setBusy(false)
    }
  }, [])

  /** Must run from a click: browsers only show the permission prompt for a user gesture. */
  const enable = useCallback(
    () =>
      run(async () => {
        const permission = await Notification.requestPermission()
        if (permission !== "granted") return permission === "denied" ? "denied" : "disabled"
        const { publicKey } = await apiGet<Schemas["VapidKeyResponse"]>("/api/v1/push/vapid-public-key")
        const registration = await navigator.serviceWorker.ready
        const subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: keyToBytes(publicKey),
        })
        try {
          await register(subscription)
        } catch (failure) {
          await subscription.unsubscribe() // never leave a browser subscription the server lacks
          throw failure
        }
        return "enabled"
      }),
    [run],
  )

  const disable = useCallback(
    () =>
      run(async () => {
        const registration = await navigator.serviceWorker.ready
        const subscription = await registration.pushManager.getSubscription()
        if (subscription) {
          await apiDelete(`/api/v1/push/subscriptions/${await register(subscription)}`)
          await subscription.unsubscribe()
        }
        return "disabled"
      }),
    [run],
  )

  return { state, busy, error, enable, disable }
}
