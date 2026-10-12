"use client"

import { ChevronDown } from "lucide-react"
import { useState } from "react"
import { LoadMore } from "@/components/load-more"
import { QueryState } from "@/components/query-state"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { useAuditLog, type AuditAction, type AuditEntry } from "@/features/users/api"
import { formatStamp } from "@/lib/format"
import { cn } from "@/lib/utils"

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

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false)
  const details = Object.keys(entry.details).length > 0
  return (
    <li className="px-3 py-2 text-sm">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{label(entry.action)}</span>
        <span className="font-medium">{entry.userEmail ?? "no user"}</span>
        {entry.resourceType ? (
          <span className="text-muted-foreground">
            {entry.resourceType}
            {entry.resourceId ? ` ${entry.resourceId}` : ""}
          </span>
        ) : null}
      </p>
      <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground tabular-nums">
        <time dateTime={entry.at}>{formatStamp(entry.at)}</time>
        {entry.ip ? <span className="font-mono">{entry.ip}</span> : null}
      </p>
      {details ? (
        <>
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            className="mt-1 inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {open ? "Hide details" : "View details"}
            <ChevronDown aria-hidden className={cn("size-3.5", open && "rotate-180")} />
          </button>
          {open ? (
            <pre className="mt-1 overflow-x-auto font-mono text-xs break-all whitespace-pre-wrap text-muted-foreground">
              {JSON.stringify(entry.details, null, 2)}
            </pre>
          ) : null}
        </>
      ) : null}
    </li>
  )
}

export function AuditLog() {
  const [action, setAction] = useState<AuditAction | "">("")
  const query = useAuditLog({ action: action || undefined })

  return (
    <section className="flex max-w-3xl flex-col gap-3">
      <label className="flex w-fit flex-col gap-1 text-xs text-muted-foreground">
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
        emptyLayout="line"
      >
        {(data) => (
          <ul className="flex flex-col divide-y divide-border rounded-md border border-border">
            {data.items.map((entry) => (
              <AuditRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </QueryState>
      <LoadMore query={query} />
    </section>
  )
}
