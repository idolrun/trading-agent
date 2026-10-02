import type { ReactNode } from "react"
import { words } from "@/features/cards/types"
import type { Card } from "@/features/cards/types"
import type { Schemas } from "@/lib/api/types"

type Span = Schemas["SpanOut"]

export const sourceAnchor = (messageId: string) => `source-${messageId}`

/**
 * Message text with each literal span marked. Offsets are Unicode code points (the API's Python
 * indices), so the text is sliced as code points, not UTF-16 units. A span outside the text is
 * dropped; where spans overlap ("357.5" inside "357.5C") each piece carries every field
 * covering it.
 */
export function highlightSpans(text: string, spans: Span[]): ReactNode[] {
  const chars = Array.from(text)
  const valid = spans.filter(
    (span) => Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.start < span.end && span.end <= chars.length,
  )
  const cuts = [...new Set([0, chars.length, ...valid.flatMap((span) => [span.start, span.end])])].sort((a, b) => a - b)
  return cuts.slice(0, -1).map((from, index) => {
    const to = cuts[index + 1]
    const piece = chars.slice(from, to).join("")
    const fields = valid.filter((span) => span.start <= from && to <= span.end).map((span) => words(span.field))
    if (fields.length === 0) return piece
    const names = [...new Set(fields)].join(", ")
    return (
      <mark
        key={from}
        title={names}
        data-fields={names}
        className="rounded-sm bg-info-muted px-0.5 text-info-foreground underline decoration-solid underline-offset-2"
      >
        {piece}
      </mark>
    )
  })
}

/**
 * The messages the card was built from. A field the interpreter took from an earlier message
 * (context provenance) is named in the "Inferred" list and links to that message, which is
 * marked in turn, so the user can check the source before approving (7.1 step 4).
 */
export function SourceMessages({ card }: { card: Card }) {
  const { inferredFields } = card
  const sourceMessages = card.sourceMessages.filter(
    (message, index, all) => all.findIndex((m) => m.messageId === message.messageId) === index,
  )
  if (sourceMessages.length === 0 && inferredFields.length === 0) return null

  const suppliedBy = (messageId: string) =>
    inferredFields.filter((item) => item.sourceMessageId === messageId).map((item) => item.field)

  return (
    <section aria-label="Source messages" className="flex flex-col gap-2">
      {inferredFields.length > 0 ? (
        <p className="text-sm">
          <span className="font-medium">Inferred from an earlier message: </span>
          {inferredFields.map((item, index) => (
            <span key={`${item.field}-${item.sourceMessageId}`}>
              {index > 0 ? ", " : null}
              <a
                href={`#${sourceAnchor(item.sourceMessageId)}`}
                className="underline decoration-dotted underline-offset-2"
              >
                <mark className="rounded-sm bg-warning-muted px-1 text-warning-foreground">
                  {words(item.field)}
                </mark>
              </a>
            </span>
          ))}
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {sourceMessages.map((message) => {
          const supplies = suppliedBy(message.messageId)
          return (
            <li
              key={message.messageId}
              id={sourceAnchor(message.messageId)}
              data-supplies-inferred={supplies.length > 0}
              className="scroll-mt-20 border-l-2 border-border pl-3 target:border-ring data-[supplies-inferred=true]:border-warning"
            >
              <p className="flex items-baseline justify-between gap-x-3 text-xs text-muted-foreground">
                <span>{message.messageId === card.messageId ? "Signal" : "Earlier message"}</span>
                <time dateTime={message.postedAt}>
                  {new Date(message.postedAt).toLocaleTimeString("en-US", { timeStyle: "short" })}
                </time>
              </p>
              <p className="text-sm break-words whitespace-pre-wrap">
                {message.text == null ? "(no text)" : highlightSpans(message.text, message.spans)}
              </p>
              {supplies.length > 0 ? (
                <p className="mt-0.5 text-xs text-muted-foreground">Supplies: {supplies.map(words).join(", ")}</p>
              ) : null}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
