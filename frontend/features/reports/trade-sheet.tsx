"use client"

import type { ReactNode } from "react"
import { QueryState } from "@/components/query-state"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { ReportFilters } from "@/features/reports/filters"
import { money, price, providerName, stamp, words } from "@/features/reports/format"
import { useTradeDetail } from "@/features/reports/queries"
import type { TradeDetail } from "@/features/reports/types"
import { Notice, OutcomeBadge, Pnl, StatusBadge, Unavailable } from "@/features/reports/ui"

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide uppercase">{title}</h3>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-mono tabular-nums">{children}</dd>
    </div>
  )
}

function Lifecycle({ d }: { d: TradeDetail }) {
  const t = d.trade
  return (
    <div className="flex flex-col gap-5 pb-6">
      <div className="flex flex-wrap items-center gap-2">
        <OutcomeBadge outcome={t.outcome} />
        <StatusBadge status={t.status} />
        <span className="text-xs text-muted-foreground">
          Attribution: {t.attribution === "single" ? "one signal" : t.attribution === "shared" ? "several sources (not split)" : "no signal (manual or adopted)"}
        </span>
      </div>
      <Notice tone={t.outcome === "unknown" ? "warning" : "info"}>{d.outcomeExplanation}</Notice>

      <Block title="1 · Signal">
        {d.signals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No signal is linked to this position (manual, adopted from the broker, or system).</p>
        ) : (
          d.signals.map((s) => (
            <div key={s.signalId} className="rounded-md border p-3 text-sm">
              <div className="flex justify-between gap-2 text-xs text-muted-foreground">
                <span>
                  {providerName(s.providerId, s.providerName)} · {words(s.msgType)} · read by {s.interpreter}
                </span>
                <span>{stamp(s.postedAt)}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap">{s.text ?? <Unavailable reason="message text not stored" />}</p>
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-muted-foreground">Parsed fields</summary>
                <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted p-2 text-[11px]">{JSON.stringify(s.parsed, null, 2)}</pre>
              </details>
            </div>
          ))
        )}
      </Block>

      <Block title="2 · Decision and your approval">
        {d.decisions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No card is linked to this position.</p>
        ) : (
          d.decisions.map((c) => (
            <dl key={c.cardId} className="flex flex-col gap-1 rounded-md border p-3">
              <Row label="Card">
                {words(c.action)} · {words(c.cardType)} · {words(c.state)}
              </Row>
              <Row label="Engine result">{c.disabledReason ? `Disabled: ${c.disabledReason}` : "Accepted: approvable card"}</Row>
              {c.warnings.length ? <Row label="Warnings">{c.warnings.join(", ")}</Row> : null}
              <Row label="Proposed quantity">{c.proposedQty ?? <Unavailable reason="you enter it" />}</Row>
              <Row label="Your decision">
                {c.decided ? `${words(c.decided)} · ${stamp(c.decidedAt)}` : <Unavailable reason="no approval record" />}
              </Row>
              {c.decided === "approve" ? (
                <>
                  <Row label="Approved quantity">{c.approvedQty ?? <Unavailable />}</Row>
                  <Row label="Approved limit">{c.approvedLimitPrice ? price(c.approvedLimitPrice) : <Unavailable />}</Row>
                  <Row label="Edited by you">{c.edited ? "yes" : "no"}</Row>
                </>
              ) : null}
              {c.events.length ? <Row label="Card history">{c.events.map((e) => `${e.label}×${e.count}`).join(", ")}</Row> : null}
            </dl>
          ))
        )}
      </Block>

      <Block title="3 · Orders">
        <div className="flex flex-col gap-2">
          {d.orders.map((o) => (
            <div key={o.orderId} className="rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">
                  {o.side.toUpperCase()} {words(o.kind)} · {words(o.orderType)} {o.limitPrice ? `@ ${price(o.limitPrice)}` : ""}
                </span>
                <StatusBadge status={o.status} />
              </div>
              <p className="text-xs text-muted-foreground">
                Requested {o.requestedQty} · filled {o.filledQty} · remaining {o.remainingQty} · {words(o.mode)} · submitted {stamp(o.submittedAt)}
                {o.autoReason ? ` · automatic: ${words(o.autoReason)}` : ""}
              </p>
              <p className="font-mono text-[11px] text-muted-foreground">
                {o.brokerOrderId ? `Broker order ${o.brokerOrderId}` : "No broker order id"} · {o.clientOrderId}
              </p>
              <ol className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
                {o.steps.map((s) => (
                  <li key={`${s.status}-${s.at}`}>
                    {words(s.status)} ({s.source.replace("_", " ")}) {stamp(s.at)}
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      </Block>

      <Block title="4 · Fills">
        <Table aria-label="Fills">
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Side</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead className="text-right">Fee</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {d.fills.map((f) => (
              <TableRow key={f.fillId}>
                <TableCell>{stamp(f.filledAt)}</TableCell>
                <TableCell>{f.side.toUpperCase()}</TableCell>
                <TableCell className="text-right tabular-nums">{f.qty}</TableCell>
                <TableCell className="text-right tabular-nums">{price(f.price)}</TableCell>
                <TableCell className="text-right">
                  <Unavailable reason="not captured" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">Average prices are quantity-weighted over these fills.</p>
      </Block>

      <Block title="5 · Position and result">
        <dl className="flex flex-col gap-1 rounded-md border p-3">
          <Row label="Bought / sold / still open">
            {t.entryQty} / {t.exitQty} / {t.openQty}
          </Row>
          <Row label="Average entry · exit">
            {price(t.avgEntryPrice)} · {price(t.avgExitPrice)}
          </Row>
          <Row label="Spent on entries">{money(t.entryCost)}</Row>
          <Row label="Realized">
            <Pnl value={t.realizedPnl} />
          </Row>
          <Row label="Unrealized (now)">{t.status === "open" ? <Pnl value={t.unrealizedPnl} /> : "n/a · closed"}</Row>
          <Row label="Fees">
            <Unavailable reason="not captured" />
          </Row>
          <Row label="Net (before fees)">
            <Pnl value={t.netPnl} />
          </Row>
          <Row label="Held">{t.closedAt ? `${stamp(t.openedAt)} → ${stamp(t.closedAt)}` : `since ${stamp(t.openedAt)}`}</Row>
        </dl>
        <Table aria-label="Position changes">
          <TableHeader>
            <TableRow>
              <TableHead>Change</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Average cost</TableHead>
              <TableHead>Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {d.lotHistory.map((s, i) => (
              <TableRow key={`${s.change}-${i}`}>
                <TableCell>{words(s.change)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {s.qtyBefore ?? 0} → {s.qtyAfter}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {price(s.avgBefore)} → {price(s.avgAfter)}
                </TableCell>
                <TableCell>{stamp(s.at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Block>

      <Block title="6 · Stops, targets and audit trail">
        {d.stops.length ? (
          <ul className="text-sm">
            {d.stops.map((s, i) => (
              <li key={i}>
                {words(s.kind)} at {price(s.level)} · {s.qty} · {words(s.status)} · held by {s.heldBy}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No stop or target is recorded.</p>
        )}
        {d.audit.length ? (
          <ul className="text-xs text-muted-foreground">
            {d.audit.map((a, i) => (
              <li key={i}>
                {stamp(a.at)} · {words(a.action)} · {a.userEmail ?? "system"}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No audit-log entries reference this trade.</p>
        )}
        <p className="text-xs text-muted-foreground">An exit reason is recorded only for automatic exits (see the order above); it is not stored for manual or signal exits.</p>
      </Block>
    </div>
  )
}

export function TradeSheet({
  filters,
  lotId,
  onClose,
}: {
  filters: ReportFilters
  lotId: string | null
  onClose: () => void
}) {
  const query = useTradeDetail(filters, lotId)
  return (
    <Sheet open={lotId != null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Trade details</SheetTitle>
          <SheetDescription>The lifecycle recorded for this position. Missing steps are shown as missing, never filled in.</SheetDescription>
        </SheetHeader>
        <div className="px-4">
          <QueryState query={query} loadingLabel="Loading trade" emptyTitle="Trade not found">
            {(d) => <Lifecycle d={d} />}
          </QueryState>
        </div>
      </SheetContent>
    </Sheet>
  )
}
