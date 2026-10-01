"use client"

import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"

import { useState } from "react"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { useAuditLog, type AuditAction } from "@/features/users/api"


const ACTIONS: AuditAction[] = [
  "login_success",
  "login_failed",
  "logout",
  "step_up_success",
  "step_up_failure",
  "rate_limited",
  "password_changed",
  "password_reset",
  "user_created",
  "user_deactivated",
  "user_reactivated",
  "trade_approved",
  "trade_rejected",
  "card_edited",
  "order_created",
  "order_cancelled",
  "lot_adjusted",
  "kill_switch",
]

const label = (action: string) => action.replaceAll("_", " ")

export function AuditLog() {
  const [action, setAction] = useState<AuditAction | "">("")
  const query = useAuditLog({ action: action || undefined })

  return (
    <section className="flex flex-col gap-3">
      <label className="flex w-fit flex-col gap-1 text-sm">
        Action
        <NativeSelect value={action} onChange={(event) => setAction(event.target.value as AuditAction | "")}>
          <NativeSelectOption value="">All</NativeSelectOption>
          {ACTIONS.map((value) => (
            <NativeSelectOption key={value} value={value}>
              {label(value)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </label>
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading audit log"
        emptyTitle="Nothing recorded"
      >
        {(data) => (
          <ul className="flex flex-col gap-2">
            {data.items.map((entry) => (
              <li key={entry.id} className="flex flex-col gap-0.5 rounded-lg border border-border px-3 py-2 text-sm">
                <p>
                  <span className="mr-2 text-xs uppercase tracking-wide text-muted-foreground">{label(entry.action)}</span>
                  {entry.userEmail ?? "no user"}
                  {entry.resourceType ? ` · ${entry.resourceType} ${entry.resourceId ?? ""}` : ""}
                </p>
                <p className="text-xs text-muted-foreground">
                  {new Date(entry.at).toLocaleString()}
                  {entry.ip ? ` · ${entry.ip}` : ""}
                  {Object.keys(entry.details).length > 0 ? ` · ${JSON.stringify(entry.details)}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <LoadMore query={query} />
    </section>
  )
}
