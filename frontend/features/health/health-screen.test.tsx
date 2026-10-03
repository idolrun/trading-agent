import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { HealthScreen } from "@/features/health/health-screen"
import { healthFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const rowsOf = (table: HTMLElement) => within(table).getAllByRole("row").slice(1)

describe("HealthScreen", () => {
  it("renders the metrics with units", async () => {
    renderWithQuery(<HealthScreen />)

    const latency = await screen.findByRole("table", { name: "Latency by stage" })
    expect(within(rowsOf(latency)[0]).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "total", "fast", "40", "120 ms", "340.5 ms", "900 ms",
    ])
    expect(rowsOf(latency)).toHaveLength(2)

    const queue = screen.getByRole("table", { name: "Queue wait per provider" })
    expect(within(queue).getByText("111222333444555666")).toBeInTheDocument()
    expect(within(queue).getByText("210 ms")).toBeInTheDocument()

    const backlog = screen.getByRole("table", { name: "Backlog" })
    expect(within(backlog).getByText("7 messages")).toBeInTheDocument()
    expect(within(backlog).getByText("4.5 s")).toBeInTheDocument()

    const messages = screen.getByRole("table", { name: "Messages" })
    expect(within(messages).getByRole("row", { name: /Duplicates since start 6/ })).toBeInTheDocument()

    const interp = screen.getByRole("table", { name: "Interpretation" })
    expect(within(interp).getByRole("row", { name: /Settled by code \(fast path\) 25%/ })).toBeInTheDocument()
    expect(within(interp).getByRole("row", { name: /wrong-link rate\) 0 \(0%\)/ })).toBeInTheDocument()
    const models = screen.getByRole("table", { name: "LLM models" })
    expect(within(models).getByText("claude-haiku")).toBeInTheDocument()
    expect(within(models).getByText("10%")).toBeInTheDocument()
    expect(within(models).getByText("$1.2345")).toBeInTheDocument()

    const providers = screen.getByRole("table", { name: "LLM providers" })
    expect(within(providers).getByText("open")).toBeInTheDocument()

    expect(screen.getByText("$12.50 of $50.00 (25%)")).toBeInTheDocument()

    expect(within(screen.getByRole("table", { name: "Card outcomes" })).getByText("User approved")).toBeInTheDocument()
    const orders = screen.getByRole("table", { name: "Orders by status" })
    expect(within(orders).getByRole("row", { name: /Partially filled 1/ })).toBeInTheDocument()

    const recon = screen.getByRole("table", { name: "Reconciliation" })
    expect(within(recon).getByText("broker timeout")).toBeInTheDocument()
    expect(within(recon).getByRole("row", { name: /Clean no/ })).toBeInTheDocument()

    const infra = screen.getByRole("table", { name: "Invariants and database" })
    expect(within(infra).getByRole("row", { name: /Invariant blocks 2/ })).toBeInTheDocument()
  })

  it("re-queries when the window changes", async () => {
    const windows: (string | null)[] = []
    server.use(
      http.get("*/api/v1/health/details", ({ request }) => {
        const window = new URL(request.url).searchParams.get("window")
        windows.push(window)
        return HttpResponse.json({ ...healthFixture, window, latency: window === "7d" ? [] : healthFixture.latency })
      }),
    )
    renderWithQuery(<HealthScreen />)
    await screen.findByRole("table", { name: "Latency by stage" })
    await userEvent.selectOptions(screen.getByLabelText("Window"), "7d")
    await screen.findByText("No data in this window.", undefined, { timeout: 3000 })
    expect(windows).toEqual(["session", "7d"])
  })
})
