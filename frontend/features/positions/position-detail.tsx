"use client"

import Link from "next/link"
import { Badge } from "@/components/badge"
import { QueryState } from "@/components/query-state"
import { words } from "@/features/cards/types"
import { positionLabel } from "@/features/positions/position-list"
import { usePosition } from "@/features/positions/queries"
import { formatContract, formatPrice, formatTime } from "@/lib/format"

export function PositionDetail({ positionId }: { positionId: string }) {
  const query = usePosition(positionId)

  return (
    <QueryState query={query} loadingLabel="Loading position" emptyTitle="Position not found">
      {(position) => (
        <div className="flex flex-col gap-6">
          <section className="flex flex-col gap-1 rounded-lg border border-border p-4 text-sm">
            <p className="flex flex-wrap items-center gap-2 text-base font-medium">
              {positionLabel(position)}
              {position.isCorrected ? <Badge>Corrected</Badge> : null}
            </p>
            <p className="text-muted-foreground">
              Provider {position.providerId} · <span className="capitalize">{position.status}</span> · opened{" "}
              {formatTime(position.openedAt)}
            </p>
            <p className="tabular-nums">
              Entry {formatPrice(position.providerEntryPrice)} · remaining {position.remainingFraction}
            </p>
            {position.stopLevel ? (
              <p className="tabular-nums">
                Stop {position.stopLevel} ({position.stopBasis ?? "basis unknown"})
              </p>
            ) : null}
            {position.tpLevels.length > 0 ? (
              <p className="tabular-nums">Take profit {position.tpLevels.join(", ")}</p>
            ) : null}
          </section>

          <section aria-label="Lots" className="flex flex-col gap-2">
            <h2 className="text-lg font-medium">Lots</h2>
            {position.lots.length === 0 ? (
              <p className="text-sm text-muted-foreground">No lots follow this position.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {position.lots.map((lot) => (
                  <li key={lot.id} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm">
                    <Link href={`/lots/${lot.id}`} className="underline underline-offset-2">
                      {formatContract(lot.contract)}
                    </Link>
                    <span className="tabular-nums text-muted-foreground">
                      {lot.qty} @ {formatPrice(lot.avgPrice)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="Linked signals" className="flex flex-col gap-2">
            <h2 className="text-lg font-medium">Linked signals</h2>
            {position.links.length === 0 ? (
              <p className="text-sm text-muted-foreground">No signals linked.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {position.links.map((link) => (
                  <li key={`${link.signalId}:${link.createdAt}`} className="flex flex-col gap-0.5 rounded-lg border border-border px-3 py-2 text-sm">
                    <span className="break-all">Signal {link.signalId}</span>
                    <span className="text-muted-foreground">
                      Method: {words(link.method)} · Confidence: {link.confidence ?? "n/a"} · {words(link.outcome)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="History" className="flex flex-col gap-2">
            <h2 className="text-lg font-medium">History</h2>
            {position.events.length === 0 ? (
              <p className="text-sm text-muted-foreground">No history yet.</p>
            ) : (
              <ol className="flex flex-col gap-2">
                {position.events.map((event, index) => (
                  <li key={`${event.at}:${index}`} className="rounded-lg border border-border px-3 py-2 text-sm">
                    <p className="flex flex-wrap justify-between gap-2">
                      <span className="font-medium">{words(event.change)}</span>
                      <time dateTime={event.at} className="text-xs text-muted-foreground tabular-nums">
                        {formatTime(event.at)}
                      </time>
                    </p>
                    {event.before || event.after ? (
                      <p className="text-xs break-all text-muted-foreground">
                        {JSON.stringify(event.before ?? null)} → {JSON.stringify(event.after ?? null)}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </QueryState>
  )
}
