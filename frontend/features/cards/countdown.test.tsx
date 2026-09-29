import { act, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { Countdown } from "@/features/cards/countdown"

describe("Countdown", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] })
    vi.setSystemTime(new Date("2026-09-29T14:00:00Z"))
  })
  afterEach(() => vi.useRealTimers())

  it("counts down in a polite live region and greys out at expiry", () => {
    render(<Countdown validUntil="2026-09-29T14:02:30Z" />)
    const status = screen.getByText("3 min left").closest("p")!
    expect(status).toHaveAttribute("aria-live", "polite")
    expect(status).toHaveAttribute("data-expired", "false")

    act(() => vi.advanceTimersByTime(100_000))
    expect(screen.getByText("Under 1 min left")).toBeInTheDocument()

    act(() => vi.advanceTimersByTime(60_000))
    expect(screen.getByText("Expired")).toBeInTheDocument()
    expect(status).toHaveAttribute("data-expired", "true")
    expect(status).toHaveClass("text-muted-foreground")
  })

  it("renders nothing without a validUntil", () => {
    const { container } = render(<Countdown validUntil={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
