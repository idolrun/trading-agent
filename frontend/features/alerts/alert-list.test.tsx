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
})
