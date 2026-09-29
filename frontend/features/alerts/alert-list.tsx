"use client"

import Link from "next/link"
import { useState } from "react"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { useAcknowledgeAlert } from "@/features/alerts/mutations"
import { useAlertList } from "@/features/alerts/queries"
import type { AlertSeverity } from "@/features/alerts/types"

const FIELD =
  "h-9 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"

const SEVERITIES: AlertSeverity[] = ["urgent", "warning", "info"]

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
          <select
            className={FIELD}
            value={severity}
            onChange={(event) => setSeverity(event.target.value as AlertSeverity | "")}
          >
            <option value="">All</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showAcknowledged}
            onChange={(event) => setShowAcknowledged(event.target.checked)}
          />
          Show acknowledged
        </label>
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
                className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
              >
                <p className="text-sm">
                  <span className="mr-2 text-xs uppercase tracking-wide text-muted-foreground">
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
