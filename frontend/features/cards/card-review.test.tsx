import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { CardDetailScreen } from "@/features/cards/card-detail-screen"
import type { CardDetail } from "@/features/cards/types"
import { cardFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

function detail(overrides: Omit<Partial<CardDetail>, "proposal"> & { proposal?: Partial<CardDetail["proposal"]> } = {}): CardDetail {
  const { proposal, ...rest } = overrides
  return {
    ...cardFixture,
    events: [],
    approval: null,
    sourceMessages: [],
    ...rest,
    proposal: { ...cardFixture.proposal, ...proposal },
  }
}

function serve(card: CardDetail) {
  server.use(
    http.get("*/api/v1/cards/:id", () => HttpResponse.json(card)),
    http.get("*/api/v1/cards/:id/preflight", () => HttpResponse.json({ applicable: false, verdict: "review", checks: [] })),
  )
}

describe("card review", () => {
  it("shows the trade and keeps approve off while the underlying is only suggested", async () => {
    serve(
      detail({
        underlying: null,
        contract: { instrument: "option", underlying: "MU", expiry: "2026-10-16", strike: "550", right: "call" },
        cardType: "review",
        proposal: {
          qty: null,
          price: "1.25",
          missingFields: ["underlying_confirmation"],
          suggestedUnderlying: "MU",
          reviewReasons: ["underlying_missing"],
        },
        sourceMessages: [
          {
            messageId: cardFixture.messageId!,
            authorId: "1",
            postedAt: cardFixture.postedAt,
            text: "BTO 550c 1.25",
            spans: [],
          },
        ],
      }),
    )
    renderWithQuery(<CardDetailScreen cardId={cardFixture.id} />)
    const article = await screen.findByRole("article")
    expect(within(article).getByRole("heading", { level: 1 })).toHaveTextContent("Unknown underlying")
    expect(within(article).getByText("Not confirmed")).toBeInTheDocument()
    expect(within(article).getByText("Suggested, not validated")).toBeInTheDocument()
    expect(within(article).getByRole("region", { name: "Original message" })).toHaveTextContent("BTO 550c 1.25")
    expect(within(article).getByRole("button", { name: "Approve" })).toBeDisabled()
    expect(within(article).getByText(/does not approve the trade/)).toBeInTheDocument()
    expect(within(article).getAllByText("MU").length).toBeGreaterThan(0)
  })

  it("offers no approve on a disabled card and says why", async () => {
    serve(
      detail({
        state: "disabled",
        cardType: "disabled",
        proposal: { disabledReason: "tier cannot buy one contract" },
      }),
    )
    renderWithQuery(<CardDetailScreen cardId={cardFixture.id} />)
    const article = await screen.findByRole("article")
    expect(within(article).getByText("tier cannot buy one contract")).toBeInTheDocument()
    expect(within(article).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument()
    expect(within(article).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument()
  })

  it("keeps lifecycle history behind Details", async () => {
    serve(
      detail({
        events: [
          {
            at: "2026-09-29T14:05:00Z",
            actor: "user",
            fromState: "pending",
            toState: "approved",
            reasonCode: "user_approved",
            detail: {},
          },
        ],
      }),
    )
    renderWithQuery(<CardDetailScreen cardId={cardFixture.id} />)
    const article = await screen.findByRole("article")
    expect(within(article).queryByText("User approved")).not.toBeInTheDocument()
    await userEvent.click(within(article).getByRole("button", { name: "Details" }))
    expect(await within(article).findByRole("region", { name: "Lifecycle" })).toHaveTextContent("User approved")
  })
})
