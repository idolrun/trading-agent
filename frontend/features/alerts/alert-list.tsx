"use client"

import { ChevronDown, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { AlertBell, type SeverityCounts } from "@/features/alerts/alert-bell"
import { groupAlerts, type AlertGroup } from "@/features/alerts/group"
import { useAcknowledgeAlert } from "@/features/alerts/mutations"
import { useAlertList } from "@/features/alerts/queries"
import { alertTitle } from "@/features/alerts/titles"
import type { AlertSeverity } from "@/features/alerts/types"
import { formatStamp } from "@/lib/format"
import { cn } from "@/lib/utils"

const SEVERITIES: AlertSeverity[] = ["urgent", "warning", "info"]

const SEVERITY: Record<AlertSeverity, { icon: LucideIcon; row: string; tone: string; label: string }> = {
  urgent: { icon: OctagonAlert, row: "border-destructive/50 bg-destructive/10", tone: "text-destructive", label: "Urgent" },
  warning: { icon: TriangleAlert, row: "border-warning/60 bg-warning/10", tone: "text-warning-foreground", label: "Warning" },
  info: { icon: Info, row: "border-border", tone: "text-info-foreground", label: "Info" },
}

/** Acknowledged = seen by a person. The backend has no "resolved" state, so the UI never claims one. */
const ACK_NOTE = "Acknowledged means someone has seen it. It does not clear a live safety condition, and it is not resolved."

/** Rows in the desk panel before the rest move to the alerts page. */
const COMPACT_GROUPS = 5

function GroupRow({
  group,
  onAcknowledge,
  isPending,
}: {
  group: AlertGroup
  onAcknowledge: (ids: string[]) => void
  isPending: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const { icon: Icon, row, tone, label } = SEVERITY[group.severity]
  const open = group.open.length > 0
  const repeats = group.events.length
  const title = alertTitle(group.kind)
  const state = open ? (group.open.length < repeats ? `${group.open.length} open` : "Open") : "Acknowledged"

  return (
    <li
      data-severity={group.severity}
      data-state={open ? "open" : "acknowledged"}
      className={cn("rounded-md border px-3 py-2 text-sm", open ? row : "border-border text-muted-foreground")}
    >
      <div className="flex items-start gap-2">
        <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", open ? tone : "text-muted-foreground")} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className={cn("text-[11px] font-semibold tracking-wider uppercase", open ? tone : "")}>{label}</span>
            <span className={cn("font-medium break-words", open ? "text-foreground" : "")}>{title}</span>
          </p>
          <p className="mt-0.5 break-words text-muted-foreground">{group.message}</p>
          <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground tabular-nums">
            {repeats > 1 ? (
              <>
                <span>{repeats} occurrences</span>
                <span>First seen {formatStamp(group.firstAt)}</span>
                <span>Last seen {formatStamp(group.lastAt)}</span>
              </>
            ) : (
              <time dateTime={group.lastAt}>{formatStamp(group.lastAt)}</time>
            )}
            <span title={ACK_NOTE}>{state}</span>
            {group.cardId ? (
              <Link href={`/cards/${group.cardId}`} className="underline underline-offset-2">
                View card
              </Link>
            ) : null}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
              className="inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              {expanded ? "Hide events" : repeats > 1 ? `View ${repeats} events` : "View details"}
              <ChevronDown aria-hidden className={cn("size-3.5", expanded && "rotate-180")} />
            </button>
            {open ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8"
                disabled={isPending}
                onClick={() => onAcknowledge(group.open.map((alert) => alert.id))}
              >
                {isPending ? "Acknowledging…" : group.open.length > 1 ? `Acknowledge all (${group.open.length})` : "Acknowledge"}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
      {expanded ? (
        <div className="mt-2 ml-6 flex flex-col gap-2 border-l border-border pl-3 text-xs">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-muted-foreground">
            <dt>Kind</dt>
            <dd className="text-foreground">{group.kind}</dd>
            <dt>Message</dt>
            <dd className="break-words whitespace-pre-wrap text-foreground">{group.message}</dd>
          </dl>
          <p className="text-muted-foreground">
            The alert record has no incident id, order, or diagnostic payload. Each event below is the audit row.
          </p>
          <ul className="flex flex-col gap-1.5">
            {group.events.map((event) => (
              <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 text-muted-foreground">
                <span className="min-w-0">
                  <time dateTime={event.createdAt} className="tabular-nums">
                    {formatStamp(event.createdAt)}
                  </time>
                  {event.acknowledged ? " · Acknowledged" : " · Open"}
                  <span className="mt-0.5 block font-mono break-all text-[11px]">{event.id}</span>
                </span>
                {event.acknowledged ? null : (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8 px-2 text-xs"
                    disabled={isPending}
                    aria-label={`Acknowledge event from ${formatStamp(event.createdAt)}`}
                    onClick={() => onAcknowledge([event.id])}
                  >
                    Acknowledge
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  )
}

/** `compact` is the desk panel: a heading, repeats grouped, the filters left to the alerts page. */
export function AlertList({ compact = false }: { compact?: boolean }) {
  const [severity, setSeverity] = useState<AlertSeverity | "">("")
  const [showAcknowledged, setShowAcknowledged] = useState(false)
  const query = useAlertList({
    severity: severity || undefined,
    acknowledged: showAcknowledged ? undefined : false,
  })
  const acknowledge = useAcknowledgeAlert()
  const ackAll = (ids: string[]) => ids.forEach((id) => acknowledge.mutate(id))
  const loaded = query.data?.items ?? []
  const openIncidents = groupAlerts(loaded.filter((alert) => !alert.acknowledged)).length
  // One count per severity (open events), so an urgent alert is never folded into a bigger total.
  const counts: SeverityCounts = { urgent: 0, warning: 0, info: 0 }
  for (const alert of loaded) if (!alert.acknowledged) counts[alert.severity as AlertSeverity] += 1
  // The desk panel opens by default while anything urgent is unacknowledged; the user's toggle then wins.
  const [chosen, setChosen] = useState<boolean | null>(null)
  const open = !compact || (chosen ?? counts.urgent > 0)

  return (
    <Collapsible open={open} onOpenChange={setChosen} asChild>
    <section aria-labelledby={compact ? "alerts-heading" : undefined} className="flex flex-col gap-2">
      {compact ? (
        <div className="flex items-center justify-between gap-3">
          <CollapsibleTrigger className="group/alerts flex min-h-9 items-center gap-2 text-left">
            <h2 id="alerts-heading" className="text-sm font-semibold tracking-wide uppercase">
              Alerts
            </h2>
            <AlertBell counts={counts} />
            <ChevronDown aria-hidden className="size-4 text-muted-foreground transition-transform group-data-[state=open]/alerts:rotate-180" />
          </CollapsibleTrigger>
          <Link href="/alerts" className="text-xs underline underline-offset-2">
            All alerts
          </Link>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
            <p role="status" className="text-sm tabular-nums">
              {query.data ? `${openIncidents} open ${openIncidents === 1 ? "incident" : "incidents"}` : "Open incidents"}
            </p>
            <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
              <label className="flex w-fit flex-col gap-1 text-sm">
                Severity
                <NativeSelect
                  value={severity}
                  onChange={(event) => setSeverity(event.target.value as AlertSeverity | "")}
                >
                  <NativeSelectOption value="">All</NativeSelectOption>
                  {SEVERITIES.map((value) => (
                    <NativeSelectOption key={value} value={value}>
                      {SEVERITY[value].label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </label>
              <Label className="min-h-9 font-normal leading-normal select-text">
                <Checkbox checked={showAcknowledged} onCheckedChange={(value) => setShowAcknowledged(value === true)} />
                Show acknowledged
              </Label>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{ACK_NOTE}</p>
        </div>
      )}
      <CollapsibleContent className="flex flex-col gap-2">
      {acknowledge.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {acknowledge.error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        compact={compact}
        emptyLayout="line"
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading alerts"
        emptyTitle={showAcknowledged ? "No alerts" : "No open alerts"}
      >
        {(data) => {
          const groups = groupAlerts(data.items)
          const rows = compact ? groups.slice(0, COMPACT_GROUPS) : groups
          return (
            <>
              <ul className="flex flex-col gap-1.5">
                {rows.map((group) => (
                  <GroupRow key={group.key} group={group} onAcknowledge={ackAll} isPending={acknowledge.isPending} />
                ))}
              </ul>
              {compact && groups.length > rows.length ? (
                <Link href="/alerts" className="text-xs underline underline-offset-2">
                  {groups.length - rows.length} more in All alerts
                </Link>
              ) : null}
              {!compact && data.nextCursor ? (
                <p className="text-xs text-muted-foreground">
                  Showing the latest {data.items.length}. Older alerts are still stored; narrow the severity to see a different slice.
                </p>
              ) : null}
            </>
          )
        }}
      </QueryState>
      </CollapsibleContent>
    </section>
    </Collapsible>
  )
}
