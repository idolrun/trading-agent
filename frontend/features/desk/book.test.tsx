import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { LotDetail } from "@/features/lots/lot-detail"
import { OrderDetail } from "@/features/orders/order-detail"
import { OrderList } from "@/features/orders/order-list"
import { PositionDetail } from "@/features/positions/position-detail"
import { PositionList } from "@/features/positions/position-list"
import { lotDetailFixture, orderFixture, positionFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const AVG = "system average — may differ from broker cost basis"

describe("lot detail", () => {
  it("labels the average, lists stops and orders, and links the order", async () => {
    renderWithQuery(<LotDetail lotId={lotDetailFixture.id} />)
    expect(await screen.findByText(new RegExp(AVG))).toBeInTheDocument()
    expect(screen.getByText(/stop at/i)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "entry" })).toHaveAttribute("href", `/orders/${orderFixture.id}`)
    expect(screen.queryByText(/read-only/i)).not.toBeInTheDocument()
  })

  it("shows a foreign lot as read-only with no action buttons", async () => {
    server.use(
      http.get("*/api/v1/lots/:id", () => HttpResponse.json({ ...lotDetailFixture, isForeign: true })),
    )
    renderWithQuery(<LotDetail lotId={lotDetailFixture.id} />)
    expect(await screen.findByText(/read-only/i)).toBeInTheDocument()
    expect(screen.getByText("Foreign")).toBeInTheDocument()
    expect(screen.getByText(new RegExp(AVG))).toBeInTheDocument()
    expect(screen.queryAllByRole("button")).toHaveLength(0)
  })
})

describe("order detail", () => {
  it("renders the state history in order, then fills", async () => {
    renderWithQuery(<OrderDetail orderId={orderFixture.id} />)
    const history = within(await screen.findByRole("region", { name: "State history" }))
    const rows = history.getAllByRole("listitem").map((li) => li.textContent ?? "")
    expect(rows).toHaveLength(3)
    expect(rows[0]).toMatch(/intent/)
    expect(rows[1]).toMatch(/submitted/)
    expect(rows[2]).toMatch(/partially filled/)
    expect(within(screen.getByRole("region", { name: "Fills" })).getByText("1 @ 1.25")).toBeInTheDocument()
  })
})

describe("order list", () => {
  it("shows mode and auto-reason badges and links each order", async () => {
    server.use(
      http.get("*/api/v1/orders", () =>
        HttpResponse.json({
          items: [
            orderFixture,
            { ...orderFixture, id: "0a1b2c3d-0000-4000-8000-0000000000b2", mode: "live", autoReason: "stop_hit", kind: "exit" },
          ],
          nextCursor: null,
        }),
      ),
    )
    renderWithQuery(<OrderList />)
    expect(await screen.findByText("shadow")).toBeInTheDocument()
    expect(screen.getByText("live")).toBeInTheDocument()
    expect(screen.getByText("Auto: Stop hit")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "exit" })).toHaveAttribute("href", "/orders/0a1b2c3d-0000-4000-8000-0000000000b2")
  })

  it("sends the status filter to the server", async () => {
    const seen: (string | null)[] = []
    server.use(
      http.get("*/api/v1/orders", ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("status"))
        return HttpResponse.json({ items: [orderFixture], nextCursor: null })
      }),
    )
    renderWithQuery(<OrderList />)
    await screen.findByText("shadow")
    await userEvent.selectOptions(screen.getByLabelText("Status"), "filled")
    await waitFor(() => expect(seen).toContain("filled"))
  })

  it("loads the next page", async () => {
    server.use(
      http.get("*/api/v1/orders", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor")
        return cursor
          ? HttpResponse.json({ items: [{ ...orderFixture, id: "0a1b2c3d-0000-4000-8000-0000000000b3", kind: "exit" }], nextCursor: null })
          : HttpResponse.json({ items: [orderFixture], nextCursor: "c1" })
      }),
    )
    renderWithQuery(<OrderList />)
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findByRole("link", { name: "exit" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })
})

describe("positions", () => {
  it("groups by provider (ids stay strings) and filters by status", async () => {
    const big = "1234567890123456789"
    const seen: (string | null)[] = []
    server.use(
      http.get("*/api/v1/positions", ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("status"))
        return HttpResponse.json({
          items: [
            positionFixture,
            { ...positionFixture, id: "0a1b2c3d-0000-4000-8000-0000000000d2", providerId: big, isCorrected: true },
            { ...positionFixture, id: "0a1b2c3d-0000-4000-8000-0000000000d3", underlying: "QQQ" },
          ],
          nextCursor: null,
        })
      }),
    )
    renderWithQuery(<PositionList />)
    const one = within(await screen.findByRole("region", { name: "Provider 1" }))
    expect(one.getAllByRole("link")).toHaveLength(2)
    const other = within(screen.getByRole("region", { name: `Provider ${big}` }))
    expect(other.getAllByRole("link")).toHaveLength(1)
    expect(other.getByText("Corrected")).toBeInTheDocument()
    expect(one.queryByText("Corrected")).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText("Status"), "closed")
    await waitFor(() => expect(seen).toContain("closed"))
  })

  it("shows history, linked signals with method and confidence, and lots", async () => {
    renderWithQuery(<PositionDetail positionId={positionFixture.id} />)
    expect(await screen.findByText(/Method: Reply chain · Confidence: 0.95/)).toBeInTheDocument()
    const history = within(screen.getByRole("region", { name: "History" }))
    expect(history.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringMatching(/^Opened/),
      expect.stringMatching(/^Trimmed/),
    ])
    expect(within(screen.getByRole("region", { name: "Lots" })).getByRole("link")).toHaveAttribute(
      "href",
      "/lots/0a1b2c3d-0000-4000-8000-0000000000c1",
    )
  })
})
