import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { Toaster } from "@/components/toaster"
import { CardInbox } from "@/features/cards/card-inbox"
import type { Card, CardDecision } from "@/features/cards/types"
import { cardFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const soon = () => new Date(Date.now() + 5 * 60_000).toISOString()

type CardOverrides = Omit<Partial<Card>, "proposal"> & { proposal?: Partial<Card["proposal"]> }

function card(overrides: CardOverrides = {}): Card {
  const { proposal, ...rest } = overrides
  return {
    ...cardFixture,
    validUntil: soon(),
    ...rest,
    proposal: { ...cardFixture.proposal, ...proposal },
  }
}

/** A stateful stand-in for the server: lists `cards`, records decisions, answers with `reply`. */
function serve(cards: Card[], reply?: (body: CardDecision, current: Card) => Response | Card) {
  const decisions: { id: string; body: CardDecision }[] = []
  let listCalls = 0
  server.use(
    http.get("*/api/v1/cards", ({ request }) => {
      const state = new URL(request.url).searchParams.get("state")
      if (state === "pending") listCalls += 1
      return HttpResponse.json({ items: cards.filter((c) => c.state === state), nextCursor: null })
    }),
    http.post("*/api/v1/cards/:id/decision", async ({ request, params }) => {
      const body = (await request.json()) as CardDecision
      const id = String(params.id)
      decisions.push({ id, body })
      const current = cards.find((c) => c.id === id)!
      const result = reply ? reply(body, current) : { ...current, state: "approved" as const }
      if (result instanceof Response) return result
      cards.splice(cards.indexOf(current), 1, result)
      return HttpResponse.json({ ...result, events: [], approval: null })
    }),
  )
  return { decisions, listCalls: () => listCalls }
}

const approveButton = () => screen.getByRole("button", { name: "Approve" })

describe("card inbox", () => {
  it("shows the empty state, and follows nextCursor with Load more", async () => {
    server.use(http.get("*/api/v1/cards", () => HttpResponse.json({ items: [], nextCursor: null })))
    const { unmount } = renderWithQuery(<CardInbox />)
    expect(await screen.findByText("No cards waiting")).toBeInTheDocument()
    unmount()

    const second = card({ id: "0a1b2c3d-0000-4000-8000-000000000002", underlying: "QQQ" })
    server.use(
      http.get("*/api/v1/cards", ({ request }) => {
        const { searchParams } = new URL(request.url)
        const cursor = searchParams.get("cursor")
        if (searchParams.get("state") !== "pending") return HttpResponse.json({ items: [], nextCursor: null })
        return cursor
          ? HttpResponse.json({ items: [second], nextCursor: null })
          : HttpResponse.json({ items: [card()], nextCursor: "next-1" })
      }),
    )
    renderWithQuery(<CardInbox />)
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findAllByRole("article")).toHaveLength(2)
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it("shows author, contract, qty x price, warnings and an advisory note; approve is one click", async () => {
    const { decisions } = serve([card({ note: "Provider says: scale out", providerId: "42" })])
    renderWithQuery(<CardInbox />)
    expect(await screen.findByText("Provider 42")).toBeInTheDocument()
    expect(screen.getByText("2026-10-16")).toBeInTheDocument()
    expect(screen.getByText("2 × 1.25")).toBeInTheDocument()
    expect(screen.getByText("Signal is 4 minutes old")).toBeInTheDocument()
    expect(screen.getByText("Advisory")).toBeInTheDocument()

    await userEvent.click(approveButton())
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body).toEqual({ decision: "approve", version: 1 })
  })

  it("never shows a hidden advisory note", async () => {
    serve([card({ note: null, noteHidden: true })])
    renderWithQuery(<CardInbox />)
    await screen.findByRole("article")
    expect(screen.queryByText("Advisory")).not.toBeInTheDocument()
  })

  it("AC-15: a trim with a blank qty says so and cannot be approved until a qty is entered", async () => {
    const { decisions } = serve([
      card({ action: "trim", proposal: { qty: null, price: null, labels: ["lot_too_small"] } }),
    ])
    renderWithQuery(<CardInbox />)
    expect(await screen.findByText(/lot too small to trim: exit all or skip/i)).toBeInTheDocument()
    expect(approveButton()).toBeDisabled()

    await userEvent.type(screen.getByLabelText("Quantity"), "1")
    expect(approveButton()).toBeEnabled()
    await userEvent.click(approveButton())
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body).toEqual({ decision: "approve", version: 1, edits: { qty: 1 } })
  })

  it("AC-14: a suggested underlying is never pre-confirmed; review completion is an edit, then approve", async () => {
    const review = card({
      cardType: "review",
      underlying: null,
      proposal: { missingFields: ["underlying_confirmation"], suggestedUnderlying: "GOOGL" },
    })
    const { decisions } = serve([review], (body, current) =>
      body.decision === "edit"
        ? { ...current, version: 2, proposal: { ...current.proposal, missingFields: [] } }
        : { ...current, state: "approved" },
    )
    renderWithQuery(<CardInbox />)
    const confirm = await screen.findByRole("checkbox", { name: /confirm the underlying is GOOGL/i })
    expect(confirm).not.toBeChecked()
    expect(approveButton()).toBeDisabled()
    expect(screen.getByRole("button", { name: "Complete review" })).toBeDisabled()

    await userEvent.click(confirm)
    await userEvent.click(screen.getByRole("button", { name: "Complete review" }))
    await waitFor(() => expect(approveButton()).toBeEnabled())
    expect(decisions[0].body).toEqual({
      decision: "edit",
      version: 1,
      edits: { confirmedUnderlying: "GOOGL" },
    })
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()

    await userEvent.click(approveButton())
    await waitFor(() => expect(decisions).toHaveLength(2))
    expect(decisions[1].body).toEqual({ decision: "approve", version: 2 })
  })

  it("AC-16: the TP editor blocks legs larger than the qty and a split that does not add up", async () => {
    const { decisions } = serve([
      card({
        proposal: {
          qty: 3,
          tpLegs: [
            { level: "1.50", qty: 2 },
            { level: "1.75", qty: 1 },
          ],
        },
      }),
    ])
    renderWithQuery(<CardInbox />)
    const tp1 = await screen.findByLabelText("TP1 quantity")
    const tp2 = screen.getByLabelText("TP2 quantity")
    expect(approveButton()).toBeEnabled()

    await userEvent.clear(tp1)
    await userEvent.type(tp1, "4")
    expect(screen.getByRole("alert")).toHaveTextContent(/cannot be larger than the order quantity \(3\)/i)
    expect(approveButton()).toBeDisabled()

    await userEvent.clear(tp1)
    await userEvent.type(tp1, "1")
    expect(screen.getByRole("alert")).toHaveTextContent(/add up to 2; they must add up to 3/i)
    expect(approveButton()).toBeDisabled()

    await userEvent.clear(tp2)
    await userEvent.type(tp2, "2")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    await userEvent.click(approveButton())
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body.edits).toEqual({ tpSplit: [1, 2] })
  })

  it("sends the breakeven choice with the approval, and only once one is picked", async () => {
    const { decisions } = serve([
      card({
        action: "stop",
        proposal: {
          qty: null,
          price: null,
          stop: { cancel: false, level: null, basis: "premium", heldBy: "broker", candleClose: false },
          breakevenOptions: { userAvg: "1.25", providerEntry: "1.20" },
        },
      }),
    ])
    renderWithQuery(<CardInbox />)
    await screen.findByRole("article")
    expect(approveButton()).toBeDisabled()
    await userEvent.click(screen.getByRole("radio", { name: /provider entry/i }))
    await userEvent.click(approveButton())
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body.edits).toEqual({ breakevenChoice: "provider_entry" })
  })

  it("a disabled card is listed with its reason and offers no actions", async () => {
    serve([
      card({
        cardType: "disabled",
        state: "disabled",
        proposal: { disabledReason: "tier cannot buy one contract" },
      }),
    ])
    renderWithQuery(<CardInbox />)
    expect(await screen.findByText("tier cannot buy one contract")).toBeInTheDocument()
    expect(within(screen.getByRole("article")).getByText("Disabled")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument()
  })

  it("highlights inferred fields and links each to the message it came from", async () => {
    const earlier = "1234567890123450000"
    serve([
      card({
        cardType: "inferred",
        inferredFields: [{ field: "strike", sourceMessageId: earlier }],
        sourceMessages: [
          {
            messageId: cardFixture.messageId!,
            authorId: "42",
            postedAt: "2026-09-29T14:00:00Z",
            text: "SPY 550C @ 1.25",
            spans: [
              { field: "underlying", start: 0, end: 3 },
              { field: "strike", start: 4, end: 7 },
              { field: "right", start: 4, end: 8 }, // overlaps the strike
              { field: "entry_price", start: 11, end: 15 },
              { field: "stop_level", start: 20, end: 30 }, // out of range: dropped
            ],
          },
          { messageId: earlier, authorId: "42", postedAt: "2026-09-29T13:58:00Z", text: "watching 550", spans: [] },
        ],
      }),
    ])
    renderWithQuery(<CardInbox />)
    const link = await screen.findByRole("link", { name: "Strike" })
    expect(link).toHaveAttribute("href", `#source-${earlier}`)
    expect(link.querySelector("mark")).not.toBeNull()
    expect(within(screen.getByRole("heading", { level: 3 })).getByText("550").tagName).toBe("MARK") // the strike in the contract line

    // literal spans are marked at their offsets; overlapping pieces list every field
    const own = screen.getByText("Signal message", { exact: false }).closest("li")!
    const marks = [...own.querySelectorAll("mark")].map((m) => [m.textContent, m.getAttribute("title")])
    expect(marks).toEqual([
      ["SPY", "Underlying"],
      ["550", "Strike, Right"],
      ["C", "Right"],
      ["1.25", "Entry price"],
    ])
    expect(own).toHaveTextContent("SPY 550C @ 1.25")

    const source = screen.getByText("watching 550").closest("li")!
    expect(source).toHaveAttribute("id", `source-${earlier}`)
    expect(within(source).getByText("Supplies: Strike")).toBeInTheDocument()
  })

  it("409: toasts 'Card changed or expired' and refetches", async () => {
    const { listCalls } = serve([card()], () =>
      HttpResponse.json({ message: "card changed since this version; reload", code: "conflict" }, { status: 409 }),
    )
    renderWithQuery(
      <>
        <CardInbox />
        <Toaster />
      </>,
    )
    await screen.findByRole("article")
    const before = listCalls()
    await userEvent.click(approveButton())
    expect(await screen.findByText("Card changed or expired")).toBeInTheDocument()
    await waitFor(() => expect(listCalls()).toBeGreaterThan(before))
  })

  it("422: shows the server's message at the card", async () => {
    serve([card({ action: "exit", proposal: { qty: 1 } })], () =>
      HttpResponse.json({ message: "not approvable: qty_exceeds_lot", code: "invalid" }, { status: 422 }),
    )
    renderWithQuery(<CardInbox />)
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }))
    const article = screen.getByRole("article")
    expect(await within(article).findByText("not approvable: qty_exceeds_lot")).toBeInTheDocument()
  })

  it("403 step_up_required (manual entry): asks for the password and retries the approval once", async () => {
    let steps = 0
    server.use(
      http.post("*/api/v1/auth/step-up", () => {
        steps += 1
        return HttpResponse.json({ stepUpUntil: "2026-09-29T15:00:00Z" })
      }),
    )
    const { decisions } = serve([card()], (_body, current) =>
      steps === 0
        ? HttpResponse.json({ code: "step_up_required", message: "confirm your password first" }, { status: 403 })
        : { ...current, state: "approved" },
    )
    renderWithQuery(<CardInbox />)
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }))

    await userEvent.type(await screen.findByLabelText("Password"), "correct horse battery")
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }))
    await waitFor(() => expect(decisions).toHaveLength(2))
    expect(steps).toBe(1)
    expect(decisions.map((d) => d.body)).toEqual([
      { decision: "approve", version: 1 },
      { decision: "approve", version: 1 },
    ])
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(screen.queryByText("confirm your password first")).not.toBeInTheDocument()
  })

  it("keyboard: A approves and R rejects the focused card, but not while typing in a field", async () => {
    const { decisions } = serve([
      card({ action: "trim", proposal: { qty: 1 } }),
    ])
    renderWithQuery(<CardInbox />)
    const article = await screen.findByRole("article")

    await userEvent.click(screen.getByLabelText("Quantity"))
    await userEvent.keyboard("a{Backspace}r{Backspace}") // typing in the qty field
    expect(decisions).toHaveLength(0)

    article.focus()
    await userEvent.keyboard("r")
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body).toEqual({ decision: "reject", version: 1 })
  })

  it("keyboard: A approves the focused card", async () => {
    const { decisions } = serve([card()])
    renderWithQuery(<CardInbox />)
    const article = await screen.findByRole("article")
    article.focus()
    await userEvent.keyboard("a")
    await waitFor(() => expect(decisions).toHaveLength(1))
    expect(decisions[0].body.decision).toBe("approve")
  })

  it("keyboard: A does nothing while approve is unavailable", async () => {
    const { decisions } = serve([card({ action: "trim", proposal: { qty: null } })])
    renderWithQuery(<CardInbox />)
    const article = await screen.findByRole("article")
    article.focus()
    await userEvent.keyboard("a")
    expect(decisions).toHaveLength(0)
  })
})
