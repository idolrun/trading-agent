"use client"

import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import Link from "next/link"
import { useState } from "react"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { useAcknowledgeAlert } from "@/features/alerts/mutations"
import { useAlertList } from "@/features/alerts/queries"
import type { AlertSeverity } from "@/features/alerts/types"
import { cn } from "@/lib/utils"


const SEVERITIES: AlertSeverity[] = ["urgent", "warning", "info"]

const SEVERITY_ROW: Record<AlertSeverity, string> = {
  urgent: "border-destructive/50 bg-destructive/10",
  warning: "border-warning/60 bg-warning/10",
  info: "border-info/60 bg-info/10",
}

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  urgent: "text-destructive",
  warning: "text-warning-foreground",
  info: "text-info-foreground",
}

/** `compact` is the desk panel: adds its own heading and a link to the full page. */
export function AlertList({ compact = false }: { compact?: boolean }) {
  const [severity, setSeverity] = useState<AlertSeverity | "">("")
  const [showAcknowledged, setShowAcknowledged] = useState(false)
  const query = useAlertList({
    severity: severity || undefined,
    acknowledged: showAcknowledged ? undefined : false,
  })
  const acknowledge = useAcknowledgeAlert()

  return (
    <section className="flex flex-col gap-3">
      {compact ? (
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-lg font-medium">Alerts</h2>
          <Link href="/alerts" className="text-sm underline underline-offset-2">
            All alerts
          </Link>
        </div>
      ) : null}
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
                {value}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        <Label className="min-h-9 font-normal leading-normal select-text">
          <Checkbox checked={showAcknowledged} onCheckedChange={(value) => setShowAcknowledged(value === true)} />
          Show acknowledged
        </Label>
      </div>
      {acknowledge.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {acknowledge.error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading alerts"
        emptyTitle={showAcknowledged ? "No alerts" : "No open alerts"}
      >
        {(data) => (
          <ul className="flex flex-col gap-2">
            {data.items.map((alert) => (
              <li
                key={alert.id}
                className={cn(
                  "flex flex-col gap-2 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between",
                  SEVERITY_ROW[alert.severity as AlertSeverity],
                )}
              >
                <p className="text-sm">
                  <span
                    className={cn(
                      "mr-2 text-xs font-medium uppercase tracking-wide",
                      SEVERITY_LABEL[alert.severity as AlertSeverity],
                    )}
                  >
                    {alert.severity}
                  </span>
                  {alert.message}
                  {alert.cardId ? (
                    <Link
                      href={`/cards/${alert.cardId}`}
                      className="ml-2 underline underline-offset-2"
                    >
                      View card
                    </Link>
                  ) : null}
                </p>
                {alert.acknowledged ? (
                  <Badge>Acknowledged</Badge>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={acknowledge.isPending && acknowledge.variables === alert.id}
                    onClick={() => acknowledge.mutate(alert.id)}
                  >
                    Acknowledge
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </section>
  )
}
