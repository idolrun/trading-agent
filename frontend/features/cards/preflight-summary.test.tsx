import { screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { PreflightSummary } from "@/features/cards/preflight-summary"
import { preflightFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

describe("PreflightSummary", () => {
  it("shows each server check and the numbers behind them", async () => {
    renderWithQuery(<PreflightSummary cardId="c1" />)
    expect(await screen.findByRole("region", { name: "Live checks" })).toBeInTheDocument()
    expect(screen.getByText("Market hours")).toBeInTheDocument()
    expect(screen.getByText("Quote is recent")).toBeInTheDocument()
    expect(screen.getAllByText("PASS").length).toBeGreaterThan(0)
    expect(screen.getByText(/\$0\.70 \/ \$0\.71/)).toBeInTheDocument()
    expect(screen.getByText(/\$40,000\.00 → \$39,038\.00/)).toBeInTheDocument() // buying power, then after
    expect(screen.getByText(/\$1,000\.00 → \$1,962\.00/)).toBeInTheDocument() // exposure, then after
  })

  it("shows what blocks the trade, from the server's own reason", async () => {
    server.use(
      http.get("*/api/v1/cards/:id/preflight", () =>
        HttpResponse.json({
          ...preflightFixture,
          verdict: "blocked",
          checks: [
            { name: "quote_fresh", status: "blocked", reason: "quote_stale", detail: "quote from 14:00:00" },
            { name: "risk", status: "review", reason: "risk unknown: account unavailable", detail: null },
          ],
        }),
      ),
    )
    renderWithQuery(<PreflightSummary cardId="c1" />)
    expect((await screen.findAllByText("BLOCKED")).length).toBe(2) // the verdict and the check
    expect(screen.getByText(/quote from 14:00:00/)).toBeInTheDocument()
    expect(screen.getByText("REVIEW")).toBeInTheDocument()
  })

  it("says so when the checks cannot be loaded, and never blocks the decision itself", async () => {
    server.use(http.get("*/api/v1/cards/:id/preflight", () => HttpResponse.json({ message: "x", code: "unavailable" }, { status: 503 })))
    renderWithQuery(<PreflightSummary cardId="c1" />)
    expect(await screen.findByRole("alert")).toHaveTextContent(/revalidated on the server/)
  })

  it("renders nothing for a card with nothing to check", async () => {
    server.use(http.get("*/api/v1/cards/:id/preflight", () => HttpResponse.json({ ...preflightFixture, applicable: false, checks: [] })))
    const { container } = renderWithQuery(<PreflightSummary cardId="c1" />)
    await screen.findByRole("status").catch(() => null)
    expect(container.querySelector("section")).toBeNull()
  })
})
