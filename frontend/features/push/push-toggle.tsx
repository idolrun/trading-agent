"use client"

import { useSyncExternalStore } from "react"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { usePushSubscription } from "@/features/push/use-push-subscription"

/** iOS only allows web push for an app added to the Home Screen (TRD §3). */
function needsInstall() {
  const ios =
    /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  const standalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  return ios && !standalone
}

const subscribeNever = () => () => {}

export function PushToggle() {
  const { state, busy, error, enable, disable } = usePushSubscription()
  const install = useSyncExternalStore(subscribeNever, needsInstall, () => false)

  if (state === "checking") return null
  const hint = install
    ? "On iPhone, tap Share, then Add to Home Screen, and open the app from there to enable notifications."
    : state === "unsupported"
      ? "Notifications are not supported in this browser."
      : state === "denied"
        ? "Notifications are blocked. Allow them in the browser's site settings."
        : null

  return (
    <div className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted-foreground">
      {(state === "disabled" || state === "enabled") && (
        <Label className="gap-2 text-xs font-normal leading-normal select-text">
          <Switch
            checked={state === "enabled"}
            disabled={busy}
            aria-label={state === "enabled" ? "Disable notifications" : "Enable notifications"}
            onCheckedChange={(on) => void (on ? enable() : disable())}
          />
          {state === "enabled" ? "Notifications on" : "Notifications off"}
        </Label>
      )}
      {hint && <p>{hint}</p>}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
