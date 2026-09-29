import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { Toaster } from "@/components/toaster"
import { LabelQueue } from "@/features/labels/label-queue"
import { removeSignal } from "@/features/labels/types"
import type { QueueItem } from "@/features/labels/types"
import { positionFixture, queueItemFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const item = (over: Partial<QueueItem> = {}): QueueItem => ({ ...queueItemFixture, ...over })

/** Serves `items` as the queue and records every PUT (event key + body). Saved items leave the queue. */
function serve(items: QueueItem[]) {
  const puts: { eventKey: string; body: { label: { signals: { idx: number; fields: Record<string, unknown> }[]; link?: { idx: number; providerPositionId: string } | null } } }[] = []
  const queueRequests: string[] = []
  server.use(
    http.get("*/api/v1/labels/queue", () => {
      queueRequests.push("get")
      const saved = puts.map((p) => p.eventKey)
      return HttpResponse.json({ items: items.filter((i) => !saved.includes(i.eventKey)), nextCursor: null })
    }),
    http.put("*/api/v1/labels/:eventKey", async ({ request, params }) => {
      const body = (await request.json()) as (typeof puts)[number]["body"]
      puts.push({ eventKey: params.eventKey as string, body })
      return HttpResponse.json({ ...queueItemFixture, labels: [] })
    }),
  )
  return { puts, queueRequests }
}

const twoSignals: QueueItem = item({
  prelabel: {
    signals: [
      { idx: 0, msgType: "entry", instrument: "option", fields: { underlying: "SPY" } },
      { idx: 1, msgType: "add", instrument: "option", fields: { strike: "550" } },
    ],
    link: { idx: 1, providerPositionId: positionFixture.id },
  },
})

describe("label queue", () => {
  it("shows progress against the minimums from the API", async () => {
    renderWithQuery(<LabelQueue />)
    expect(await screen.findByText("actionable 12 / 500")).toBeInTheDocument()
    expect(screen.getByText("non-actionable 30 / 2000")).toBeInTheDocument()
    expect(screen.getByText("providers 2 / 5")).toBeInTheDocument()
    expect(screen.getByRole("progressbar", { name: "providers" })).toHaveAttribute("aria-valuenow", "2")
  })

  it("shows the reply parent before other context, and highlights field values in the text", async () => {
    renderWithQuery(<LabelQueue />)
    await screen.findByRole("button", { name: "Confirm" })
    const context = screen.getAllByText(/^(Replied to|Earlier)/).map((el) => el.textContent)
    expect(context[0]).toMatch(/^Replied to/)
    const text = screen.getByTestId("message-text")
    expect([...text.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["SPY", "550", "1.20"])
  })

  it("confirm sends the prelabel unchanged", async () => {
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].eventKey).toBe(queueItemFixture.eventKey)
    expect(puts[0].body.label).toEqual(queueItemFixture.prelabel)
    expect(await screen.findByText("Queue empty")).toBeInTheDocument()
  })

  it("disables Confirm without a prelabel; No signal sends empty signals", async () => {
    const { puts } = serve([item({ prelabel: null })])
    renderWithQuery(<LabelQueue />)
    expect(await screen.findByRole("button", { name: "Confirm" })).toBeDisabled()
    await userEvent.click(screen.getByRole("button", { name: "No signal" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].body.label.signals).toEqual([])
  })

  it("Save correction sends the edited field", async () => {
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    const strike = await screen.findByLabelText("Strike")
    await userEvent.clear(strike)
    await userEvent.type(strike, "555")
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].body.label.signals[0].fields).toEqual({ underlying: "SPY", strike: "555", right: "call", entry_price: "1.20" })
  })

  it("an added signal gets the next idx, with an added field", async () => {
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    await userEvent.click(await screen.findByRole("button", { name: "Add signal" }))
    await userEvent.selectOptions(screen.getByLabelText("Type 2"), "trim")
    await userEvent.selectOptions(screen.getByLabelText("Add field 2"), "exit_fraction")
    await userEvent.selectOptions(screen.getByLabelText("Exit fraction"), "half")
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const { signals } = puts[0].body.label
    expect(signals.map((s) => s.idx)).toEqual([0, 1])
    expect(signals[1]).toMatchObject({ idx: 1, msgType: "trim", fields: { exit_fraction: "half" } })
  })

  it("removing signal 0 renumbers the rest and moves the link with its signal", async () => {
    const { puts } = serve([twoSignals])
    renderWithQuery(<LabelQueue />)
    await userEvent.click(await screen.findByRole("button", { name: "Remove signal 1" }))
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const { signals, link } = puts[0].body.label
    expect(signals).toHaveLength(1)
    expect(signals[0]).toMatchObject({ idx: 0, msgType: "add" })
    expect(link).toEqual({ idx: 0, providerPositionId: positionFixture.id })
  })

  it("removing the linked signal drops the link", () => {
    const { signals, link } = removeSignal(twoSignals.prelabel!.signals, twoSignals.prelabel!.link!, 1)
    expect(signals.map((s) => s.idx)).toEqual([0])
    expect(link).toBeNull()
  })

  it("the link picker lists only this provider's positions that existed at post time", async () => {
    server.use(
      http.get("*/api/v1/positions", () =>
        HttpResponse.json({
          items: [
            { ...positionFixture, id: "p-ok", strike: "550" },
            { ...positionFixture, id: "p-other", providerId: "2", strike: "600" },
            { ...positionFixture, id: "p-later", openedAt: "2026-09-29T14:00:01Z", strike: "700" },
          ],
          nextCursor: null,
        }),
      ),
    )
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    await screen.findByRole("button", { name: "Confirm" })
    const picker = within(await screen.findByRole("group", { name: "Link to position" }))
    expect(await picker.findByRole("radio", { name: /550/ })).toBeInTheDocument()
    expect(picker.getAllByRole("radio")).toHaveLength(2) // "No link" + the one position
    expect(picker.getByRole("radio", { name: "No link" })).toBeChecked()

    await userEvent.click(picker.getByRole("radio", { name: /550/ }))
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].body.label.link).toEqual({ idx: 0, providerPositionId: "p-ok" })
  })
})

describe("label keys", () => {
  it("c confirms and the next item is shown", async () => {
    const second = item({ eventKey: "2.create.0", text: "second message", prelabel: null })
    const { puts } = serve([item(), second])
    renderWithQuery(<LabelQueue />)
    await screen.findByRole("button", { name: "Confirm" })
    await userEvent.keyboard("c")
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].eventKey).toBe(queueItemFixture.eventKey)
    expect(await screen.findByText("No prelabel for this message.")).toBeInTheDocument()
    expect(screen.getByTestId("message-text")).toHaveTextContent("second message")
  })

  it("j and k move through the queue", async () => {
    serve([item(), item({ eventKey: "2.create.0", text: "second message", prelabel: null })])
    renderWithQuery(<LabelQueue />)
    await screen.findByRole("button", { name: "Confirm" })
    await userEvent.keyboard("j")
    expect(screen.getByTestId("message-text")).toHaveTextContent("second message")
    await userEvent.keyboard("k")
    expect(screen.getByTestId("message-text")).toHaveTextContent("BTO SPY 550c 1.20")
  })

  it("n sends no signal; e focuses the first field", async () => {
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    await screen.findByRole("button", { name: "Confirm" })
    await userEvent.keyboard("e")
    expect(screen.getByLabelText("Underlying")).toHaveFocus()
    expect(screen.getByLabelText("Underlying")).toHaveValue("SPY") // the key itself was not typed
    ;(document.activeElement as HTMLElement).blur()
    await userEvent.keyboard("n")
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0].body.label.signals).toEqual([])
  })

  it("ignores keys while typing in an input", async () => {
    const { puts } = serve([item()])
    renderWithQuery(<LabelQueue />)
    const underlying = await screen.findByLabelText("Underlying")
    await userEvent.type(underlying, "cnj{Enter}")
    expect(underlying).toHaveValue("SPYcnj")
    await userEvent.type(screen.getByLabelText("Provider ID"), "c")
    expect(puts).toHaveLength(0)
  })

  it("a 409 shows a toast and refetches the queue", async () => {
    const { queueRequests } = serve([item()])
    server.use(http.put("*/api/v1/labels/:eventKey", () => HttpResponse.json({ code: "conflict", message: "stale" }, { status: 409 })))
    renderWithQuery(
      <>
        <Toaster />
        <LabelQueue />
      </>,
    )
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }))
    expect(await screen.findByText("This message was labeled elsewhere. Queue refreshed.")).toBeInTheDocument()
    await waitFor(() => expect(queueRequests.length).toBeGreaterThan(1))
  })
})
