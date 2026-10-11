import type { Coverage } from "@/features/reports/types"
import { stamp, words } from "@/features/reports/format"

/** What the numbers on screen are and are not made of. Shown under every report. */
export function CoverageNote({ coverage: c }: { coverage: Coverage }) {
  const notes = [
    `${c.window.label}. Day boundaries are ${c.window.timezone}; start included, end excluded.`,
    c.mode === "all" ? "All modes combined, simulated (shadow) fills included." : `${words(c.mode)} mode only.`,
    "Fees and commissions are not captured, so every P&L figure is before costs.",
    c.unpricedLots ? `${c.unpricedLots} open position${c.unpricedLots === 1 ? " has" : "s have"} no fresh quote, so unrealized P&L is unavailable.` : null,
    c.lotsWithoutBasis ? `${c.lotsWithoutBasis} position${c.lotsWithoutBasis === 1 ? " has" : "s have"} no recorded cost basis; ${c.lotsWithoutBasis === 1 ? "its" : "their"} P&L is unknown, not zero.` : null,
    c.reconcileAdjustments ? `${c.reconcileAdjustments} quantity change${c.reconcileAdjustments === 1 ? " was" : "s were"} made by reconciliation, not by a fill, and ${c.reconcileAdjustments === 1 ? "is" : "are"} not in realized P&L.` : null,
    c.firstEventAt ? `History starts ${stamp(c.firstEventAt)}.` : "No signals or fills are recorded yet.",
  ].filter((n): n is string => n != null)
  return (
    <details className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">
        How to read these numbers{c.unpricedLots || c.lotsWithoutBasis ? " · some data is incomplete" : ""}
      </summary>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </details>
  )
}
