import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Card, CardDecision } from "@/features/cards/types"
import { ControlRoomScreen, splitHeld } from "@/features/control-room/control-room-screen"
import { cardFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const id = (n: number) => `0a1b2c3d-0000-4000-8000-00000000000${n}`
const card = (n: number, underlying = "SPY"): Card => ({
  ...cardFixture,
  id: id(n),
  underlying,
  validUntil: new Date(Date.now() + 5 * 60_000).toISOString(),
})

describe("splitHeld", () => {
  const key = (s: string) => s
  it("holds items above the first seen one, passes older pages and removals", () => {
    expect(splitHeld(["a", "b"], null, key)).toEqual({ visible: ["a", "b"], fresh: 0, reset: true })
    const seen = new Set(["a", "b"])
    expect(splitHeld(["c", "d", "a", "b"], seen, key)).toEqual({ visible: ["a", "b"], fresh: 2, reset: false })
    expect(splitHeld(["c", "b", "z"], seen, key)).toEqual({ visible: ["b", "z"], fresh: 1, reset: false })
    expect(splitHeld(["c"], seen, key)).toEqual({ visible: ["c"], fresh: 0, reset: true })
  })
})

describe("control room", () => {
  it("approves with the typed qty, and holds new cards behind the pill", async () => {
    let cards = [card(1)]
    const decisions: CardDecision[] = []
    server.use(
      http.get("*/api/v1/cards", () => HttpResponse.json({ items: cards, nextCursor: null })),
      http.post("*/api/v1/cards/:id/decision", async ({ request }) => {
        decisions.push((await request.json()) as CardDecision)
        return HttpResponse.json({ ...cards[0], state: "approved", events: [], approval: null })
      }),
    )
    renderWithQuery(<ControlRoomScreen />)
    const article = await screen.findByRole("article", { name: /entry SPY/ })
    const approve = within(article).getByRole("button", { name: "Approve" })
    expect(approve).toBeDisabled()
    await userEvent.type(within(article).getByLabelText("Qty"), "2")
    await userEvent.click(approve)
    await waitFor(() => expect(decisions).toEqual([{ decision: "approve", version: 1, edits: { qty: 2 } }]))

    cards = [card(2, "QQQ"), card(1)]
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await userEvent.click(await screen.findByRole("button", { name: "1 new — show" }))
    expect(await screen.findByRole("article", { name: /entry QQQ/ })).toBeInTheDocument()
  })

  it("an unreadable card's Approve opens the entry sheet prefilled from the message, in place", async () => {
    const raw: Card = {
      ...card(3),
      action: "review",
      cardType: "review",
      underlying: null,
      contract: null,
      proposal: { ...cardFixture.proposal, qty: null, price: null, signalPrice: null, missingFields: [], candidatePositions: [] },
      sourceMessages: [
        { authorId: "1", messageId: cardFixture.messageId!, postedAt: cardFixture.postedAt, spans: [], text: "QQQ 0dte 748c @ .42" },
      ],
    }
    const decisions: CardDecision[] = []
    const entries: unknown[] = []
    server.use(
      http.get("*/api/v1/cards", () => HttpResponse.json({ items: [raw], nextCursor: null })),
      http.post("*/api/v1/cards/:id/decision", async ({ request }) => {
        decisions.push((await request.json()) as CardDecision)
        return HttpResponse.json({ ...raw, state: "rejected", events: [], approval: null })
      }),
      http.post("*/api/v1/manual/entry", async ({ request }) => {
        entries.push(await request.json())
        return HttpResponse.json({ cardId: id(9), state: "approved", orderIds: [] })
      }),
    )
    renderWithQuery(<ControlRoomScreen />)
    const article = await screen.findByRole("article", { name: /review/ })
    await userEvent.click(within(article).getByRole("button", { name: "Approve" }))

    const sheet = await screen.findByRole("dialog", { name: "Enter trade" })
    expect(within(sheet).getByText("QQQ 0dte 748c @ .42")).toBeInTheDocument()
    expect(within(sheet).getByLabelText("Underlying")).toHaveValue("QQQ")
    expect(within(sheet).getByLabelText("Strike")).toHaveValue(748)
    expect(within(sheet).getByRole("radio", { name: "Call" })).toHaveAttribute("aria-checked", "true")
    expect(within(sheet).getByLabelText(/Limit price/)).toHaveValue(0.42)
    await userEvent.click(within(sheet).getByRole("button", { name: "Quantity 2" }))
    await userEvent.click(within(sheet).getByRole("button", { name: "Submit entry" }))

    await waitFor(() => expect(decisions).toEqual([{ decision: "reject", version: 1 }]))
    expect(entries).toEqual([
      expect.objectContaining({
        instrument: expect.objectContaining({ instrument: "option", underlying: "QQQ", strike: "748", right: "call" }),
        qty: 2,
        limitPrice: "0.42",
      }),
    ])
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Enter trade" })).not.toBeInTheDocument())
  })
})
