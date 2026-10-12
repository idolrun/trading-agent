import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { AlertList } from "@/features/alerts/alert-list"
import { alertFixture, server, warningAlertFixture } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

/** Stateful backend: acknowledging flips the row, listing honours both filters. */
function statefulAlerts() {
  const rows = [alertFixture, warningAlertFixture].map((alert) => ({ ...alert }))
  server.use(
    http.get("*/api/v1/alerts", ({ request }) => {
      const params = new URL(request.url).searchParams
      const severity = params.get("severity")
      const acknowledged = params.get("acknowledged")
      const items = rows.filter(
        (alert) =>
          (!severity || alert.severity === severity) &&
          (acknowledged == null || String(alert.acknowledged) === acknowledged),
      )
      return HttpResponse.json({ items, nextCursor: null })
    }),
    http.post("*/api/v1/alerts/:id/acknowledge", ({ params }) => {
      const alert = rows.find((row) => row.id === params.id)!
      alert.acknowledged = true
      return HttpResponse.json(alert)
    }),
  )
}

describe("AlertList", () => {
  it("acknowledging removes the alert from the open list; it reappears under show acknowledged", async () => {
    statefulAlerts()
    renderWithQuery(<AlertList />)
    expect(await screen.findByText("Feed silent for 300s")).toBeInTheDocument()

    const [first] = screen.getAllByRole("button", { name: "Acknowledge" })
    await userEvent.click(first)
    await waitFor(() => expect(screen.queryByText("Feed silent for 300s")).not.toBeInTheDocument())
    expect(screen.getByText("Parser degraded for author 111")).toBeInTheDocument()

    await userEvent.click(screen.getByLabelText("Show acknowledged"))
    expect(await screen.findByText("Feed silent for 300s")).toBeInTheDocument()
    expect(screen.getByText("Acknowledged")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Acknowledge" })).toHaveLength(1)
  })

  it("colors each row by severity", async () => {
    statefulAlerts()
    renderWithQuery(<AlertList />)
    const urgent = (await screen.findByText("Feed silent for 300s")).closest("li")
    const warning = screen.getByText("Parser degraded for author 111").closest("li")
    expect(urgent).toHaveClass("bg-destructive/10")
    expect(warning).toHaveClass("bg-warning/10")
  })

  it("filters by severity", async () => {
    statefulAlerts()
    renderWithQuery(<AlertList />)
    await screen.findByText("Feed silent for 300s")
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "warning")
    await waitFor(() => expect(screen.queryByText("Feed silent for 300s")).not.toBeInTheDocument())
    expect(screen.getByText("Parser degraded for author 111")).toBeInTheDocument()
  })

  it("links to the card only when the alert has one", async () => {
    statefulAlerts()
    renderWithQuery(<AlertList />)
    const link = await screen.findByRole("link", { name: "View card" })
    expect(link).toHaveAttribute("href", `/cards/${warningAlertFixture.cardId}`)
    expect(screen.getAllByRole("link", { name: "View card" })).toHaveLength(1)
  })

  it("folds an identical repeat into one row, keeps each event reachable, and acknowledges them all", async () => {
    const repeats = [1, 2, 3].map((n) => ({ ...alertFixture, id: `rep-${n}`, createdAt: `2026-09-29T14:0${n}:00Z` }))
    const acked: string[] = []
    server.use(
      http.get("*/api/v1/alerts", () => HttpResponse.json({ items: repeats.filter((a) => !acked.includes(a.id)), nextCursor: null })),
      http.post("*/api/v1/alerts/:id/acknowledge", ({ params }) => {
        acked.push(String(params.id))
        return HttpResponse.json({ ...repeats[0], id: params.id, acknowledged: true })
      }),
    )
    renderWithQuery(<AlertList compact />)
    expect(await screen.findByText(/3 occurrences/)).toBeInTheDocument()
    expect(screen.getAllByText("Feed silent for 300s")).toHaveLength(1)

    await userEvent.click(screen.getByRole("button", { name: "View 3 events" }))
    expect(screen.getAllByRole("button", { name: /Acknowledge event from/ })).toHaveLength(3)

    await userEvent.click(screen.getByRole("button", { name: "Acknowledge all (3)" }))
    await waitFor(() => expect(acked.sort()).toEqual(["rep-1", "rep-2", "rep-3"]))
  })

  it("does not merge alerts whose message differs", async () => {
    server.use(
      http.get("*/api/v1/alerts", () =>
        HttpResponse.json({ items: [alertFixture, { ...alertFixture, id: "other", message: "Feed silent for 600s" }], nextCursor: null }),
      ),
    )
    renderWithQuery(<AlertList compact />)
    expect(await screen.findByText("Feed silent for 300s")).toBeInTheDocument()
    expect(screen.getByText("Feed silent for 600s")).toBeInTheDocument()
  })

  it("compact panel is collapsed with nothing open, and the header says so", async () => {
    server.use(http.get("*/api/v1/alerts", () => HttpResponse.json({ items: [], nextCursor: null })))
    renderWithQuery(<AlertList compact />)
    expect(await screen.findByText("None open")).toBeInTheDocument()
    expect(screen.queryByText("No open alerts")).not.toBeInTheDocument() // collapsed
  })

  it("compact header counts each severity apart and collapses by hand", async () => {
    server.use(
      http.get("*/api/v1/alerts", () =>
        HttpResponse.json({
          items: [alertFixture, { ...alertFixture, id: "u2", message: "other" }, warningAlertFixture, { ...warningAlertFixture, id: "i1", severity: "info", message: "fyi" }],
          nextCursor: null,
        }),
      ),
    )
    renderWithQuery(<AlertList compact />)
    expect(await screen.findByText("2 urgent")).toBeInTheDocument()
    expect(screen.getByText("1 warning")).toBeInTheDocument()
    expect(screen.getByText("1 info")).toBeInTheDocument()
    expect(screen.getByText("fyi")).toBeVisible() // urgent open: expanded
    await userEvent.click(screen.getByRole("button", { name: /Alerts/ }))
    expect(screen.queryByText("fyi")).not.toBeInTheDocument()
    expect(screen.getByText("2 urgent")).toBeInTheDocument() // counts stay visible when collapsed
  })

})
