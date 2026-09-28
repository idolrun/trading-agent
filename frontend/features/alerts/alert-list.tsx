"use client"

import { Button } from "@/components/ui/button"
import { QueryState } from "@/components/query-state"
import { useAcknowledgeAlert } from "@/features/alerts/mutations"
import { useAlertList } from "@/features/alerts/queries"

export function AlertList() {
  const query = useAlertList({ acknowledged: false })
  const acknowledge = useAcknowledgeAlert()

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">Alerts</h2>
      {acknowledge.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {acknowledge.error.message}
        </p>
      ) : null}
      <QueryState
        query={query}
        isEmpty={(data) => data.items.length === 0}
        loadingLabel="Loading alerts"
        emptyTitle="No open alerts"
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
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={acknowledge.isPending && acknowledge.variables === alert.id}
                  onClick={() => acknowledge.mutate(alert.id)}
                >
                  Acknowledge
                </Button>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </section>
  )
}
