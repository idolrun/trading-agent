import { screen, within } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { OperationalStatus, statusCells } from "@/features/health/operational-status"
import { EntriesBadge, ModeBadge } from "@/features/health/trading-badges"
import { configFixture, healthFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const CLEAN = { clean: true, error: null, lastRunAt: "2026-09-29T14:10:00Z", mismatchAlerts: 0, mismatches: 0, unknownOrders: 0, brokerPositions: null, buyingPower: null, cash: null, equity: null }
const health = (patch: Record<string, unknown> = {}) =>
  server.use(
    http.get("*/api/v1/health/details", () =>
      HttpResponse.json({ ...healthFixture, reconciliation: CLEAN, flags: {}, backlogDepth: 0, ...patch }),
    ),
  )

const cell = (name: string) => screen.findByLabelText(new RegExp(`^${name}:`))

describe("OperationalStatus", () => {
  it("healthy: three compact cells, nothing urgent, no alert role", async () => {
    health({ feedSilenceSeconds: 3.935191 })
    renderWithQuery(<OperationalStatus />)
    const feed = await cell("Feed")
    expect(feed).toHaveAttribute("data-level", "ok")
    expect(within(feed).getByText("Last message 3.9 s ago")).toBeInTheDocument() // not 3.935191s
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(within(await cell("Parser")).getByText("Healthy")).toBeInTheDocument()
  })

  it("kill switch and blocked entries are one urgent safety cell, announced as an alert", async () => {
    health({ flags: { kill_switch: true, entries_blocked: true } })
    renderWithQuery(<OperationalStatus />)
    const safety = await screen.findByRole("alert", { name: /^Trading safety/ })
    expect(safety).toHaveAttribute("data-flags", "kill_switch entries_blocked")
    expect(safety).toHaveTextContent("Kill switch on")
    expect(screen.getAllByRole("alert")).toHaveLength(1) // the same incident is not repeated elsewhere
  })

  it("feed degraded is urgent; catch-up is a warning with backlog and age", async () => {
    health({ flags: { feed_degraded: true } })
    const { unmount } = renderWithQuery(<OperationalStatus />)
    expect(await screen.findByRole("alert", { name: /^Feed/ })).toHaveTextContent(
      "Feed degraded. Messages may be missing or late.",
    )
    unmount()

    health({ flags: { catch_up: true }, catchUp: true, backlogDepth: 7, oldestUnprocessedSeconds: 252 })
    renderWithQuery(<OperationalStatus />)
    const feed = await cell("Feed")
    expect(feed).toHaveAttribute("data-level", "warning")
    expect(feed).toHaveTextContent("Backlog 7, oldest 4 min 12 s")
  })

  it("parser degraded says manual review is required", async () => {
    health({ flags: { parser_degraded: true } })
    renderWithQuery(<OperationalStatus />)
    expect(await cell("Parser")).toHaveTextContent("manual review required")
  })

  it("reconciliation mismatch and unknown orders keep the safety cell urgent", async () => {
    health({ reconciliation: { ...CLEAN, clean: false, mismatches: 3, unknownOrders: 2 } })
    renderWithQuery(<OperationalStatus />)
    const safety = await screen.findByRole("alert", { name: /^Trading safety/ })
    expect(safety).toHaveTextContent("Reconciliation mismatch (3)")
    expect(safety).toHaveTextContent("2 orders with unknown status")
  })

  it("says so when health cannot be read, instead of showing nothing", async () => {
    server.use(http.get("*/api/v1/health/details", () => HttpResponse.json({ message: "x", code: "unavailable" }, { status: 503 })))
    renderWithQuery(<OperationalStatus />)
    expect(await screen.findByRole("alert")).toHaveTextContent("Health status unavailable")
  })

  it("ignores unknown flags", () => {
    const cells = statusCells({ ...healthFixture, reconciliation: CLEAN, flags: { something_new: true } })
    expect(cells.every((c) => c.level === "ok")).toBe(true)
  })
})

describe("header badges", () => {
  it("shows the configured trading mode as text, and LIVE distinctly", async () => {
    server.use(http.get("*/api/v1/config", () => HttpResponse.json({ ...configFixture, document: { ...configFixture.document, trading_mode: "live" } })))
    renderWithQuery(<ModeBadge />)
    const badge = await screen.findByText("LIVE")
    expect(badge.closest("[data-mode]")).toHaveAttribute("data-mode", "live")
    expect(badge.closest("[role=status]")).toHaveAccessibleName(/real money/)
  })

  it("never guesses a mode it could not read", async () => {
    server.use(http.get("*/api/v1/config", () => HttpResponse.json({ message: "no", code: "forbidden" }, { status: 403 })))
    renderWithQuery(<ModeBadge />)
    expect(await screen.findByText("MODE ?")).toBeInTheDocument()
  })

  it("entries are blocked by the kill switch or the entries flag, otherwise enabled", async () => {
    health({ flags: { kill_switch: true } })
    const { unmount } = renderWithQuery(<EntriesBadge />)
    expect(await screen.findByText("Entries blocked")).toBeInTheDocument()
    unmount()
    health({ flags: {} })
    renderWithQuery(<EntriesBadge />)
    expect(await screen.findByText("Entries enabled")).toBeInTheDocument()
  })
})
