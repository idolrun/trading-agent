"use client"

import { animate, AnimatePresence, motion, useMotionValue, useReducedMotion, useSpring, useTransform, useVelocity, type AnimationPlaybackControls, type MotionValue } from "motion/react"
import { useEffect, useRef } from "react"
import type { AlertSeverity } from "@/features/alerts/types"
import { cn } from "@/lib/utils"

// Rolling-digit bell badge (adapted from the shared NotificationBell): one chip per severity
// instead of a single total, so an urgent alert is never hidden inside a bigger number.

const SWING_SPRING = { type: "spring", stiffness: 220, damping: 10, mass: 1, restDelta: 0.01 } as const
const COLUMN_SPRING = { stiffness: 400, damping: 30, mass: 0.9 }
const ENTER_SPRING = { type: "spring", stiffness: 600, damping: 20 } as const
const FADE = { duration: 0.15 } as const
const IMPULSE = 500
const MAX_VELOCITY = 900
const WINDOW = 3
const LAG = 2
const ROLL_FADE = 34
const ROLL_VELOCITY = 9
const MAX = 99

const clamp = (value: number, limit: number) => Math.max(-limit, Math.min(limit, value))
const digitOf = (value: number) => ((value % 10) + 10) % 10

export type SeverityCounts = Record<AlertSeverity, number>

/** Colour comes with a word (the chip's label), never alone. */
const CHIP: Record<AlertSeverity, { label: string; style: string }> = {
  urgent: { label: "urgent", style: "bg-destructive text-background" },
  warning: { label: "warning", style: "bg-warning text-black/85" },
  info: { label: "info", style: "bg-info text-background" },
}

function DigitColumn({ value, reduced }: { value: number; reduced: boolean }) {
  const position = useSpring(value, COLUMN_SPRING)
  const y = useTransform(position, (p) => `${-p * 100}%`)
  const velocity = useVelocity(position)
  const mask = useTransform(velocity, (v) => {
    const fade = Math.min(ROLL_FADE, (Math.abs(v) / ROLL_VELOCITY) * ROLL_FADE)
    return `linear-gradient(to bottom, transparent 0%, #000 ${fade}%, #000 ${100 - fade}%, transparent 100%)`
  })

  useEffect(() => {
    const gap = value - position.get()
    if (Math.abs(gap) > LAG) position.jump(value - Math.sign(gap) * LAG)
    if (reduced) position.jump(value)
    else position.set(value)
  }, [value, reduced, position])

  return (
    <motion.span
      className="relative inline-block h-[1em] overflow-hidden"
      style={{ width: "1ch", maskImage: reduced ? undefined : mask, WebkitMaskImage: reduced ? undefined : mask }}
    >
      <motion.span className="absolute inset-0" style={{ y }}>
        {Array.from({ length: WINDOW * 2 + 1 }, (_, i) => {
          const tile = value - WINDOW + i
          return (
            <span key={tile} className="absolute inset-x-0 flex justify-center" style={{ top: `${tile * 100}%` }}>
              {digitOf(tile)}
            </span>
          )
        })}
      </motion.span>
    </motion.span>
  )
}

function Chip({ severity, count, reduced }: { severity: AlertSeverity; count: number; reduced: boolean }) {
  const { label, style } = CHIP[severity]
  const clamped = count > MAX
  const places = clamped ? 0 : String(count).length
  return (
    <AnimatePresence initial={false}>
      {count > 0 && (
        <motion.span
          key={severity}
          layout={!reduced}
          data-severity={severity}
          title={`${count} ${label}`}
          className={cn("inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-xs font-semibold", style)}
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0, opacity: 0 }}
          transition={reduced ? FADE : ENTER_SPRING}
        >
          <span aria-hidden className="flex leading-none tabular-nums">
            {clamped ? `${MAX}+` : Array.from({ length: places }, (_, i) => {
              const place = places - 1 - i
              return <DigitColumn key={place} value={Math.floor(count / 10 ** place)} reduced={reduced} />
            })}
          </span>
          <span className="sr-only">{`${count} ${label}`}</span>
        </motion.span>
      )}
    </AnimatePresence>
  )
}

function Bell({ swing }: { swing: MotionValue<number> }) {
  return (
    <motion.svg
      viewBox="0 0 18 18"
      fill="currentColor"
      aria-hidden
      className="size-5"
      style={{ rotate: swing, transformOrigin: "50% 12%" }}
    >
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        fillOpacity={0.55}
        d="M3.5 6.5C3.5 3.46279 5.96279 1 9 1C12.0372 1 14.5 3.46279 14.5 6.5V10.75C14.5 11.4408 15.0592 12 15.75 12C16.1642 12 16.5 12.3358 16.5 12.75C16.5 13.1642 16.1642 13.5 15.75 13.5H2.25C1.83579 13.5 1.5 13.1642 1.5 12.75C1.5 12.3358 1.83579 12 2.25 12C2.94079 12 3.5 11.4408 3.5 10.75V6.5Z"
      />
      <path d="M10.2 15H7.80099C7.64999 15 7.50799 15.068 7.41299 15.185C7.31799 15.302 7.28099 15.456 7.31199 15.603C7.48499 16.425 8.17999 17 9.00099 17C9.82199 17 10.517 16.425 10.69 15.603C10.721 15.456 10.684 15.302 10.589 15.185C10.494 15.068 10.351 15 10.2 15Z" />
    </motion.svg>
  )
}

/** A bell that swings when the open total grows, with one rolling count per severity. */
export function AlertBell({ counts }: { counts: SeverityCounts }) {
  const reduced = useReducedMotion() ?? false
  const total = counts.urgent + counts.warning + counts.info
  const swing = useMotionValue(0)
  const previous = useRef(total)
  const ringing = useRef<AnimationPlaybackControls | null>(null)

  useEffect(() => {
    const delta = total - previous.current
    previous.current = total
    if (delta <= 0 || reduced) return
    const moving = swing.getVelocity()
    ringing.current = animate(swing, 0, {
      ...SWING_SPRING,
      velocity: clamp(moving + (moving > 1 ? 1 : -1) * IMPULSE, MAX_VELOCITY),
    })
  }, [total, reduced, swing])
  useEffect(() => () => ringing.current?.stop(), [])

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn(counts.urgent > 0 ? "text-destructive" : "text-muted-foreground")}>
        <Bell swing={swing} />
      </span>
      {total === 0 ? (
        <span className="text-xs font-normal text-muted-foreground normal-case">None open</span>
      ) : (
        (["urgent", "warning", "info"] as const).map((severity) => (
          <Chip key={severity} severity={severity} count={counts[severity]} reduced={reduced} />
        ))
      )}
    </span>
  )
}
