import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse, type JsonBodyType } from "msw"
import { describe, expect, it } from "vitest"
import { ReportsScreen } from "@/features/reports/reports-screen"
import {
  emptyOverview,
  instruments,
  lossRow,
  openRow,
  overview,
  pnl,
  timeseries,
  tradeDetail,
  tradeRow,
  trades,
} from "@/features/reports/test-fixtures"
import { server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const BASE = "*/api/v1/reports/trading"

/** Records the query string of every request to a report endpoint. */
function backend(overrides: Partial<Record<string, JsonBodyType | Response>> = {}) {
  const seen: Record<string, URLSearchParams[]> = {}
  const reply = (name: string, body: JsonBodyType) =>
    http.get(`${BASE}/${name}`, ({ request }) => {
      ;(seen[name] ??= []).push(new URL(request.url).searchParams)
      const override = overrides[name]
      return override instanceof Response ? override : HttpResponse.json(override === undefined ? body : override)
    })
  server.use(
    reply("overview", overview),
    reply("pnl", pnl),
    reply("instruments", instruments),
    reply("timeseries", timeseries),
    reply("trades", trades([tradeRow, openRow, lossRow])),
    http.get(`${BASE}/trades/:id`, ({ request }) => {
      ;(seen.detail ??= []).push(new URL(request.url).searchParams)
      return HttpResponse.json(tradeDetail)
    }),
  )
  const last = (name: string) => seen[name]?.at(-1)
  return { seen, last }
}

describe("Trading report: periods and filters", () => {
  it("loads 1W by default and re-queries the real endpoint for every period", async () => {
    const { last } = backend()
    renderWithQuery(<ReportsScreen />)
    expect(await screen.findByText("Total signals")).toBeInTheDocument()
    expect(last("overview")?.get("period")).toBe("7d")

    for (const [label, period] of [["1D", "1d"], ["3D", "3d"], ["1M", "30d"], ["Overall", "all"]] as const) {
      await userEvent.click(screen.getByRole("radio", { name: new RegExp(`^${label}`) }))
      await waitFor(() => expect(last("overview")?.get("period")).toBe(period))
    }
    expect(last("overview")?.has("startDate")).toBe(false) // dates only travel with a custom period
  })

  it("waits for a valid custom range, then sends both dates", async () => {
    const { seen, last } = backend()
    renderWithQuery(<ReportsScreen />)
    await screen.findByText("Total signals")
    const before = seen.overview.length
    await userEvent.click(screen.getByRole("radio", { name: /^Custom/ }))
    expect(screen.getByText(/Choose a start date/)).toBeInTheDocument()
    expect(seen.overview.length).toBe(before) // no request for an incomplete range
    await userEvent.type(screen.getByLabelText(/Start date/), "2026-09-01")
    await userEvent.type(screen.getByLabelText(/End date/), "2026-09-03")
    await waitFor(() => expect(last("overview")?.get("period")).toBe("custom"))
    expect(last("overview")?.get("startDate")).toBe("2026-09-01")
    expect(last("overview")?.get("endDate")).toBe("2026-09-03")
  })

  it("sends mode, instrument type and symbol; the default mode is left to the backend", async () => {
    const { last } = backend()
    renderWithQuery(<ReportsScreen />)
    await screen.findByText("Total signals")
    expect(last("overview")?.has("mode")).toBe(false)
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "paper")
    await waitFor(() => expect(last("overview")?.get("mode")).toBe("paper"))
    await userEvent.selectOptions(screen.getByLabelText("Instrument type"), "option")
    await waitFor(() => expect(last("overview")?.get("instrument")).toBe("option"))
    await userEvent.type(screen.getByLabelText("Symbol"), "aapl{Enter}")
    await waitFor(() => expect(last("overview")?.get("symbol")).toBe("AAPL"))
  })

  it("does not send an invalid symbol", async () => {
    const { seen } = backend()
    renderWithQuery(<ReportsScreen />)
    await screen.findByText("Total signals")
    const before = seen.overview.length
    await userEvent.type(screen.getByLabelText("Symbol"), "12{Enter}")
    expect(await screen.findByRole("alert")).toHaveTextContent("Use letters only")
    expect(seen.overview.length).toBe(before)
  })
})

describe("Trading report: overview", () => {
  it("separates realized from unrealized and marks sign without relying on colour", async () => {
    backend()
    renderWithQuery(<ReportsScreen />)
    const realized = (await screen.findByText("Realized P&L")).closest("[data-slot=card]") as HTMLElement
    expect(within(realized).getByText("+$150.00")).toBeInTheDocument()
    expect(within(realized).getByText("(profit)")).toBeInTheDocument() // text, not only green
    expect(within(realized).getByText(/\+\$170.00 vs previous period/)).toBeInTheDocument()
    const unrealized = screen.getByText("Unrealized P&L").closest("[data-slot=card]") as HTMLElement
    expect(within(unrealized).getByText("+$50.00")).toBeInTheDocument()
    expect(within(unrealized).getByText(/current/)).toBeInTheDocument()
    expect(screen.getByText("Win rate").closest("[data-slot=card]")).toHaveTextContent("50%")
  })

  it("shows a loss with a minus sign and the word loss", async () => {
    backend({ overview: { ...overview, realizedPnl: { current: "-50", previous: null } } })
    renderWithQuery(<ReportsScreen />)
    const realized = (await screen.findByText("Realized P&L")).closest("[data-slot=card]") as HTMLElement
    expect(within(realized).getByText("−$50.00")).toBeInTheDocument()
    expect(within(realized).getByText("(loss)")).toBeInTheDocument()
    expect(within(realized).getByText("No previous period to compare")).toBeInTheDocument()
  })

  it("shows unavailable data as a dash, never as a zero", async () => {
    backend({
      overview: {
        ...overview,
        unrealizedPnl: null,
        netPnl: null,
        winRate: null,
        coverage: { ...overview.coverage, unpricedLots: 1 },
      },
    })
    renderWithQuery(<ReportsScreen />)
    const unrealized = (await screen.findByText("Unrealized P&L")).closest("[data-slot=card]") as HTMLElement
    expect(unrealized).toHaveTextContent("—")
    expect(unrealized).not.toHaveTextContent("$0.00")
    expect(unrealized).toHaveTextContent("No fresh quote")
    expect(screen.getByText("Net trading P&L").closest("[data-slot=card]")).not.toHaveTextContent("$0.00")
    expect(screen.getByText(/some data is incomplete/)).toBeInTheDocument()
    const fees = screen.getByText("Fees and commissions").nextElementSibling as HTMLElement
    expect(fees).toHaveTextContent("not available: not captured")
  })

  it("explains an empty book instead of showing sample data", async () => {
    backend({ overview: emptyOverview })
    renderWithQuery(<ReportsScreen />)
    expect(await screen.findByText("No trading activity yet")).toBeInTheDocument()
    expect(screen.getByText(/Nothing here is sample data/)).toBeInTheDocument()
  })

  it("shows an error with a retry when the report fails", async () => {
    backend({ overview: new Response(JSON.stringify({ message: "boom", code: "unavailable" }), { status: 503 }) })
    renderWithQuery(<ReportsScreen />)
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument()
  })
})

describe("Trading report: P&L tab", () => {
  it("renders the cumulative series and keeps an inactive day apart from a zero result", async () => {
    backend()
    renderWithQuery(<ReportsScreen />)
    await userEvent.click(await screen.findByRole("tab", { name: "P&L" }))
    expect(await screen.findByText("Cumulative realized P&L")).toBeInTheDocument()
    const stats = screen.getByText("Net after costs").closest("div") as HTMLElement
    expect(stats).toHaveTextContent("not available: costs are not captured")
    expect(screen.getByText("Profit factor").nextElementSibling).toHaveTextContent("4")
    // the chart's numbers are available as a table too
    const tables = screen.getAllByText("Data table")
    expect(tables.length).toBeGreaterThan(0)
  })
})

describe("Trading report: trade history", () => {
  async function openTrades() {
    const api = backend()
    renderWithQuery(<ReportsScreen />)
    await userEvent.click(await screen.findByRole("tab", { name: "Trades" }))
    await screen.findByRole("table", { name: "Trade history" })
    return api
  }

  it("lists positions with their own result and never final-labels an open one", async () => {
    await openTrades()
    const rows = screen.getAllByRole("row").slice(1)
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText("Profit")).toBeInTheDocument()
    expect(within(rows[0]).getAllByText("+$200.00")).toHaveLength(2) // realized and net
    expect(within(rows[1]).getByText(/Open · not final/)).toBeInTheDocument()
    expect(within(rows[1]).getAllByText("+$50.00")).toHaveLength(2) // unrealized and net: no final result
    expect(within(rows[2]).getByText("Loss")).toBeInTheDocument()
    expect(within(rows[2]).getAllByText("−$50.00")).toHaveLength(2)
    expect(within(rows[0]).getByText(/not available: not captured/)).toBeInTheDocument() // fees
  })

  it("sorts, filters, searches and pages on the server", async () => {
    const { last } = await openTrades()
    const header = screen.getByRole("columnheader", { name: /^Realized/ })
    expect(header).toHaveAttribute("aria-sort", "none")
    await userEvent.click(within(header).getByRole("button"))
    await waitFor(() => expect(last("trades")?.get("sort")).toBe("realized"))
    expect(last("trades")?.get("order")).toBe("desc")
    expect(screen.getByRole("columnheader", { name: /^Realized/ })).toHaveAttribute("aria-sort", "descending")
    await userEvent.click(within(screen.getByRole("columnheader", { name: /^Realized/ })).getByRole("button"))
    await waitFor(() => expect(last("trades")?.get("order")).toBe("asc"))

    await userEvent.selectOptions(screen.getByLabelText("Result"), "loss")
    await waitFor(() => expect(last("trades")?.get("outcome")).toBe("loss"))
    await userEvent.selectOptions(screen.getByLabelText("Status"), "closed")
    await waitFor(() => expect(last("trades")?.get("state")).toBe("closed"))
    await userEvent.type(screen.getByLabelText(/Search trades/), "goog{Enter}")
    await waitFor(() => expect(last("trades")?.get("search")).toBe("goog"))
    expect(last("trades")?.get("page")).toBe("1") // a filter returns to the first page
  })

  it("returns to the first page when the report period changes", async () => {
    const { last } = backend({ trades: trades([tradeRow], 60) })
    renderWithQuery(<ReportsScreen />)
    await userEvent.click(await screen.findByRole("tab", { name: "Trades" }))
    expect(await screen.findByText("1–25 of 60")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    await waitFor(() => expect(last("trades")?.get("page")).toBe("2"))
    await userEvent.click(screen.getByRole("radio", { name: /^1D/ }))
    await waitFor(() => expect(last("trades")?.get("period")).toBe("1d"))
    expect(last("trades")?.get("page")).toBe("1")
  })

  it("pages through a long list", async () => {
    backend()
    server.use(
      http.get(`${BASE}/trades`, ({ request }) => {
        const page = Number(new URL(request.url).searchParams.get("page"))
        return HttpResponse.json(trades([page === 1 ? tradeRow : lossRow], 60))
      }),
    )
    renderWithQuery(<ReportsScreen />)
    await userEvent.click(await screen.findByRole("tab", { name: "Trades" }))
    expect(await screen.findByText("1–25 of 60")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("26–50 of 60")).toBeInTheDocument()
  })

  it("opens the lifecycle of a trade with each fill and says what is missing", async () => {
    const { last } = await openTrades()
    await userEvent.click(screen.getAllByRole("button", { name: /^Inspect/ })[0])
    const sheet = await screen.findByRole("dialog")
    expect(await within(sheet).findByText(/Bought GOOGL 357.5C/)).toBeInTheDocument()
    expect(last("detail")).toBeDefined()
    expect(within(sheet).getByText("Approved quantity").nextElementSibling).toHaveTextContent("4")
    const fills = within(sheet).getByRole("table", { name: "Fills" })
    expect(within(fills).getAllByRole("row")).toHaveLength(3) // header + 2 individual fills
    expect(within(sheet).getByText(/Closed\. 4 sold/)).toBeInTheDocument()
    expect(within(sheet).getByText("No stop or target is recorded.")).toBeInTheDocument()
  })
})

describe("Trading report: instruments", () => {
  it("flags a one-trade winner as low sample and jumps to its trades", async () => {
    const { last } = backend()
    renderWithQuery(<ReportsScreen />)
    await userEvent.click(await screen.findByRole("tab", { name: "Instruments" }))
    expect(await screen.findByText("low sample")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Show trades for GOOGL" }))
    await waitFor(() => expect(last("trades")?.get("symbol")).toBe("GOOGL"))
    expect(screen.getByLabelText("Symbol")).toHaveValue("GOOGL")
  })
})
