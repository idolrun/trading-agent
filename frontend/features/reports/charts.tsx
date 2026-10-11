"use client"

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  LabelList,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { bucketLabel, signedMoney } from "@/features/reports/format"

/** Series colours come from the semantic tokens; shape and sign carry the meaning as well. */
export const SERIES = {
  gain: "var(--success)",
  loss: "var(--destructive)",
  a: "var(--info)",
  b: "var(--warning)",
  muted: "var(--muted-foreground)",
} as const

const FRAME = "aspect-auto h-56 w-full"

export type Point = { name: string; value: number | null }

/** Horizontal bars for counts (rejection reasons, status mix). Values are labelled on the bars. */
export function CountBars({ data, label, color = SERIES.a }: { data: Point[]; label: string; color?: string }) {
  const config = { value: { label, color } } satisfies ChartConfig
  const height = Math.max(120, data.length * 32 + 24)
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 32 }}>
        <CartesianGrid horizontal={false} strokeOpacity={0.4} />
        <XAxis type="number" allowDecimals={false} hide />
        <YAxis dataKey="name" type="category" width={150} tickLine={false} axisLine={false} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Bar dataKey="value" fill="var(--color-value)" radius={2}>
          <LabelList dataKey="value" position="right" className="fill-foreground text-xs" />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

/** Two or more count series per bucket (accepted vs rejected, buys vs sells). */
export function GroupedBars({
  data,
  series,
}: {
  data: ({ bucket: string } & Record<string, number | string>)[]
  series: { key: string; label: string; color: string }[]
}) {
  const config: ChartConfig = Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }]))
  return (
    <ChartContainer config={config} className={FRAME}>
      <BarChart data={data} margin={{ left: 0, right: 8 }}>
        <CartesianGrid vertical={false} strokeOpacity={0.4} />
        <XAxis dataKey="bucket" tickFormatter={bucketLabel} tickLine={false} axisLine={false} minTickGap={16} />
        <YAxis allowDecimals={false} width={40} tickLine={false} axisLine={false} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} fill={`var(--color-${s.key})`} radius={2} />
        ))}
      </BarChart>
    </ChartContainer>
  )
}

const money = (v: unknown) => signedMoney(v == null ? null : String(v))

/** P&L per bucket: gains solid, losses hatched, both labelled with the signed amount. */
export function PnlBars({ data, label }: { data: Point[]; label: string }) {
  const config = { value: { label, color: SERIES.a } } satisfies ChartConfig
  return (
    <ChartContainer config={config} className={FRAME}>
      <BarChart data={data} margin={{ top: 16, left: 0, right: 8 }}>
        <defs>
          <pattern id="loss-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill={SERIES.loss} fillOpacity={0.25} />
            <line x1="0" y1="0" x2="0" y2="6" stroke={SERIES.loss} strokeWidth="3" />
          </pattern>
        </defs>
        <CartesianGrid vertical={false} strokeOpacity={0.4} />
        <XAxis dataKey="name" tickFormatter={bucketLabel} tickLine={false} axisLine={false} minTickGap={16} />
        <YAxis width={56} tickLine={false} axisLine={false} tickFormatter={(v: number) => `${v}`} />
        <ReferenceLine y={0} stroke="var(--border)" />
        <ChartTooltip
          cursor={false}
          content={<ChartTooltipContent formatter={(v) => <span className="font-mono tabular-nums">{money(v)}</span>} />}
        />
        <Bar dataKey="value" radius={2}>
          {data.map((p) => (
            <Cell key={p.name} fill={(p.value ?? 0) < 0 ? "url(#loss-hatch)" : SERIES.gain} stroke={(p.value ?? 0) < 0 ? SERIES.loss : undefined} />
          ))}
          <LabelList dataKey="value" position="top" formatter={money} className="fill-foreground text-[10px]" />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

/** Cumulative line with a zero baseline. Unknown points break the line instead of being drawn as 0. */
export function CumulativeLine({ data, label }: { data: Point[]; label: string }) {
  const config = { value: { label, color: SERIES.a } } satisfies ChartConfig
  return (
    <ChartContainer config={config} className={FRAME}>
      <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} strokeOpacity={0.4} />
        <XAxis dataKey="name" tickFormatter={bucketLabel} tickLine={false} axisLine={false} minTickGap={16} />
        <YAxis width={56} tickLine={false} axisLine={false} domain={[(min: number) => Math.min(0, min), (max: number) => Math.max(0, max)]} />
        <ReferenceLine y={0} stroke="var(--foreground)" strokeDasharray="4 3" />
        <ChartTooltip
          content={<ChartTooltipContent formatter={(v) => <span className="font-mono tabular-nums">{money(v)}</span>} />}
        />
        <Line dataKey="value" type="stepAfter" stroke="var(--color-value)" strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
      </LineChart>
    </ChartContainer>
  )
}

/** Signed money bars per instrument, best at the top. */
export function InstrumentBars({ data }: { data: Point[] }) {
  const config = { value: { label: "Net P&L", color: SERIES.a } } satisfies ChartConfig
  const height = Math.max(140, data.length * 30 + 24)
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 56 }}>
        <defs>
          <pattern id="loss-hatch-h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill={SERIES.loss} fillOpacity={0.25} />
            <line x1="0" y1="0" x2="0" y2="6" stroke={SERIES.loss} strokeWidth="3" />
          </pattern>
        </defs>
        <CartesianGrid horizontal={false} strokeOpacity={0.4} />
        <XAxis type="number" hide />
        <YAxis dataKey="name" type="category" width={110} tickLine={false} axisLine={false} />
        <ReferenceLine x={0} stroke="var(--border)" />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel formatter={(v) => <span className="font-mono tabular-nums">{money(v)}</span>} />} />
        <Bar dataKey="value" radius={2}>
          {data.map((p) => (
            <Cell key={p.name} fill={(p.value ?? 0) < 0 ? "url(#loss-hatch-h)" : SERIES.gain} stroke={(p.value ?? 0) < 0 ? SERIES.loss : undefined} />
          ))}
          <LabelList dataKey="value" position="right" formatter={money} className="fill-foreground text-xs" />
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}
