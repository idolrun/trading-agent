"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { QueryState } from "@/components/query-state"
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { apiGet } from "@/lib/api/client"
import { CountBars, GroupedBars, SERIES } from "@/features/reports/charts"
import { CoverageNote } from "@/features/reports/coverage-note"
import { isReady, toParams, type ReportFilters } from "@/features/reports/filters"
import { count, money, percent, price, stamp, words } from "@/features/reports/format"
import { useOrders, type OrderParams } from "@/features/reports/queries"
import type { OrdersReport, TimeseriesReport } from "@/features/reports/types"
import { ChartFrame, EmptyNote, Pager, Section, Stat, Stats, StatusBadge, Unavailable } from "@/features/reports/ui"

const PAGE_SIZE = 25
const STATUSES = ["intent", "submitted", "working", "partially_filled", "filled", "cancelled", "rejected", "unknown"]

function FillActivity({ filters }: { filters: ReportFilters }) {
  const params = { ...toParams(filters), metrics: "fills_buy,fills_sell" }
  const query = useQuery({
    queryKey: ["reports", "fill-activity", params],
    queryFn: () => apiGet<TimeseriesReport>("/api/v1/reports/trading/timeseries", params),
    staleTime: 30_000,
    enabled: isReady(filters),
  })
  return (
    <QueryState query={query} loadingLabel="Loading activity" emptyTitle="No fills in this period" isEmpty={(t) => t.points.every((p) => !p.hasActivity)}>
      {(t) => {
        const data = t.points.map((p) => ({
          bucket: p.bucket,
          buys: Number(p.values.fills_buy ?? 0),
          sells: Number(p.values.fills_sell ?? 0),
        }))
        return (
          <ChartFrame
            title="Buy vs sell fills"
            description={`Actual fills per ${t.granularity}`}
            summary={`${data.reduce((n, p) => n + p.buys, 0)} buy fills and ${data.reduce((n, p) => n + p.sells, 0)} sell fills.`}
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Bucket</TableHead>
                    <TableHead className="text-right">Buys</TableHead>
                    <TableHead className="text-right">Sells</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((p) => (
                    <TableRow key={p.bucket}>
                      <TableCell>{p.bucket}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.buys}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.sells}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            }
          >
            <GroupedBars
              data={data}
              series={[
                { key: "buys", label: "Buy fills", color: SERIES.a },
                { key: "sells", label: "Sell fills", color: SERIES.b },
              ]}
            />
          </ChartFrame>
        )
      }}
    </QueryState>
  )
}

function Charts({ r, filters }: { r: OrdersReport; filters: ReportFilters }) {
  const notional = r.notionalByBucket.map((b) => ({ bucket: b.bucket, buy: Number(b.buy), sell: Number(b.sell) }))
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <ChartFrame
        title="Order status"
        description="Orders created in the period"
        summary={r.statusDistribution.map((s) => `${words(s.key)}: ${s.count}`).join(", ") || "No orders."}
        table={
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Orders</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.statusDistribution.map((s) => (
                <TableRow key={s.key}>
                  <TableCell>{s.label}</TableCell>
                  <TableCell className="text-right tabular-nums">{s.count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        }
      >
        <CountBars data={r.statusDistribution.map((s) => ({ name: s.label, value: s.count }))} label="Orders" />
      </ChartFrame>
      <FillActivity filters={filters} />
      <ChartFrame
        title="Executed notional"
        description="Price × quantity × contract multiplier of actual fills, per bucket"
        summary={`Buy ${money(String(notional.reduce((n, p) => n + p.buy, 0)))}, sell ${money(String(notional.reduce((n, p) => n + p.sell, 0)))}.`}
        table={
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead className="text-right">Buy</TableHead>
                <TableHead className="text-right">Sell</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {notional.map((p) => (
                <TableRow key={p.bucket}>
                  <TableCell>{p.bucket}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(String(p.buy))}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(String(p.sell))}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        }
      >
        <GroupedBars
          data={notional}
          series={[
            { key: "buy", label: "Buy", color: SERIES.a },
            { key: "sell", label: "Sell", color: SERIES.b },
          ]}
        />
      </ChartFrame>
      <ChartFrame
        title="Executed quantity by instrument"
        description="Filled quantity and quantity-weighted average price"
        summary={r.bySymbol.slice(0, 5).map((s) => `${s.symbol}: bought ${s.buyQty}, sold ${s.sellQty}`).join("; ") || "No fills."}
        table={
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead className="text-right">Bought</TableHead>
                <TableHead className="text-right">Avg buy</TableHead>
                <TableHead className="text-right">Sold</TableHead>
                <TableHead className="text-right">Avg sell</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.bySymbol.map((s) => (
                <TableRow key={s.symbol}>
                  <TableCell>{s.symbol}</TableCell>
                  <TableCell className="text-right tabular-nums">{s.buyQty}</TableCell>
                  <TableCell className="text-right tabular-nums">{price(s.avgBuyPrice)}</TableCell>
                  <TableCell className="text-right tabular-nums">{s.sellQty}</TableCell>
                  <TableCell className="text-right tabular-nums">{price(s.avgSellPrice)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        }
      >
        <GroupedBars
          data={r.bySymbol.slice(0, 12).map((s) => ({ bucket: s.symbol, bought: s.buyQty, sold: s.sellQty }))}
          series={[
            { key: "bought", label: "Bought", color: SERIES.a },
            { key: "sold", label: "Sold", color: SERIES.b },
          ]}
        />
      </ChartFrame>
    </div>
  )
}

export function ExecutionSection({ filters }: { filters: ReportFilters }) {
  const [params, setParams] = useState<OrderParams>({ page: 1, side: "", status: "" })
  const query = useOrders(filters, { ...params, pageSize: PAGE_SIZE })
  const set = (patch: Partial<OrderParams>) => setParams({ ...params, page: 1, ...patch })
  return (
    <QueryState
      query={query}
      loadingLabel="Loading execution analytics"
      emptyTitle="No orders in this period"
      emptyDescription="Orders appear after you approve a card and it is submitted."
      isEmpty={(r) => r.totalRows === 0 && r.submitted === 0}
    >
      {(r) => (
        <div className="flex flex-col gap-6">
          <Section title="Execution" description="What was actually submitted and filled. Requested and filled quantities are kept apart.">
            <Stats>
              <Stat label="Submitted">{count(r.submitted)}</Stat>
              <Stat label="Filled">{count(r.filled)}</Stat>
              <Stat label="Partially filled">{count(r.partiallyFilled)}</Stat>
              <Stat label="Rejected">{count(r.rejected)}</Stat>
              <Stat label="Cancelled">{count(r.cancelled)}</Stat>
              <Stat label="Pending">{count(r.pending)}</Stat>
              <Stat label="Order-to-fill rate">{percent(r.orderToFillRate)}</Stat>
              <Stat label="Buy qty (requested / filled)">
                {r.buyQtyRequested} / {r.buyQtyFilled}
              </Stat>
              <Stat label="Sell qty (requested / filled)">
                {r.sellQtyRequested} / {r.sellQtyFilled}
              </Stat>
              <Stat label="Executed notional">{money(r.executedNotional)}</Stat>
              <Stat label="Fees">
                <Unavailable reason="not captured" />
              </Stat>
            </Stats>
          </Section>
          <Charts r={r} filters={filters} />
          {r.brokerRejections.length ? (
            <Section title="Broker rejections" description="The broker's status is kept; its free-text reason is not stored.">
              <Table aria-label="Broker rejections">
                <TableHeader>
                  <TableRow>
                    <TableHead>Broker status</TableHead>
                    <TableHead className="text-right">Orders</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.brokerRejections.map((b) => (
                    <TableRow key={b.reason}>
                      <TableCell>{b.reason}</TableCell>
                      <TableCell className="text-right tabular-nums">{b.count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Section>
          ) : null}
          <Section
            title="Order activity"
            actions={
              <div className="flex flex-wrap gap-3 text-xs">
                <label className="flex items-center gap-2">
                  Side
                  <NativeSelect size="sm" value={params.side} onChange={(e) => set({ side: e.target.value as OrderParams["side"] })}>
                    <NativeSelectOption value="">Buy and sell</NativeSelectOption>
                    <NativeSelectOption value="buy">Buy</NativeSelectOption>
                    <NativeSelectOption value="sell">Sell</NativeSelectOption>
                  </NativeSelect>
                </label>
                <label className="flex items-center gap-2">
                  Status
                  <NativeSelect size="sm" value={params.status} onChange={(e) => set({ status: e.target.value })}>
                    <NativeSelectOption value="">Any status</NativeSelectOption>
                    {STATUSES.map((s) => (
                      <NativeSelectOption key={s} value={s}>
                        {words(s)}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </label>
              </div>
            }
          >
            {r.rows.length === 0 ? (
              <EmptyNote>No orders match these filters.</EmptyNote>
            ) : (
              <Table aria-label="Order activity">
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">Requested</TableHead>
                    <TableHead className="text-right">Filled</TableHead>
                    <TableHead className="text-right">Avg fill</TableHead>
                    <TableHead className="text-right">Notional</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Fees</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {r.rows.map((o) => (
                    <TableRow key={o.orderId}>
                      <TableCell>{stamp(o.at)}</TableCell>
                      <TableCell className="font-mono text-xs">{o.symbol}</TableCell>
                      <TableCell>{o.side.toUpperCase()}</TableCell>
                      <TableCell className="text-right tabular-nums">{o.requestedQty}</TableCell>
                      <TableCell className="text-right tabular-nums">{o.filledQty}</TableCell>
                      <TableCell className="text-right tabular-nums">{o.avgFillPrice ? price(o.avgFillPrice) : <Unavailable reason="nothing filled" />}</TableCell>
                      <TableCell className="text-right tabular-nums">{o.executedNotional ? money(o.executedNotional) : <Unavailable reason="nothing filled" />}</TableCell>
                      <TableCell>
                        <StatusBadge status={o.status} />
                      </TableCell>
                      <TableCell>{words(o.mode)}</TableCell>
                      <TableCell>
                        <Unavailable reason="not captured" />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <Pager page={params.page} pageSize={PAGE_SIZE} total={r.totalRows} onPage={(page) => setParams({ ...params, page })} />
          </Section>
          <CoverageNote coverage={r.coverage} />
        </div>
      )}
    </QueryState>
  )
}
