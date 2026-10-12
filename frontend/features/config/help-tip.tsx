"use client"

import { useState } from "react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { HELP, type Help } from "@/features/config/help"

const ROWS: [keyof Help, string][] = [
  ["means", "What it means"],
  ["affects", "What it affects"],
  ["example", "Example"],
  ["constraints", "Accepted values"],
  ["risk", "Risk"],
  ["important", "Important"],
]

/**
 * "?" button next to a label. Hover opens it, click/tap/Enter/Space toggles it (so touch and
 * keyboard work), Escape closes it. A popover rather than a tooltip: the text is long.
 */
export function HelpTip({ name, label }: { name: string; label: string }) {
  // hover shows it briefly; a click (or tap, Enter, Space) pins it open
  const [mode, setMode] = useState<"closed" | "hover" | "pinned">("closed")
  const help = HELP[name]
  if (!help) return null
  return (
    <Popover open={mode !== "closed"} onOpenChange={(next) => setMode(next ? "pinned" : "closed")}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Help: ${label}`}
          onPointerEnter={(event) => event.pointerType === "mouse" && setMode((current) => (current === "closed" ? "hover" : current))}
          onPointerLeave={(event) => event.pointerType === "mouse" && setMode((current) => (current === "hover" ? "closed" : current))}
          onClick={(event) => {
            if (mode !== "hover") return
            event.preventDefault() // keep the hover-opened popover and pin it
            setMode("pinned")
          }}
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-full border border-border text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <span aria-hidden="true">?</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="max-h-[70dvh] w-[min(24rem,calc(100vw-2rem))] gap-2 overflow-y-auto text-xs"
      >
        <p className="text-sm font-medium">{label}</p>
        <dl className="flex flex-col gap-1.5">
          {ROWS.map(([key, title]) =>
            help[key] ? (
              <div key={key}>
                <dt className="font-medium">{title}</dt>
                <dd className="text-muted-foreground">{help[key]}</dd>
              </div>
            ) : null,
          )}
        </dl>
        <p className="font-mono text-[11px] text-muted-foreground">key: {name}</p>
      </PopoverContent>
    </Popover>
  )
}
