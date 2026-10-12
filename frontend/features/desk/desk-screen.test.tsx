import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { CardInbox } from "@/features/cards/card-inbox"
import { DeskScreen } from "@/features/desk/desk-screen"
import { renderWithQuery } from "@/test/render"

describe("screens against the real contract shapes", () => {
  it("renders the card inbox from a page of cards", async () => {
    renderWithQuery(<CardInbox />)
    expect(await screen.findByText("SPY")).toBeInTheDocument()
    expect(screen.getByText("Signal is 4 minutes old")).toBeInTheDocument()
    expect(screen.getByLabelText("Quantity")).toHaveValue(null) // the user types it
    expect(screen.getByText("Price").nextElementSibling).toHaveTextContent("$1.25")
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled() // until a quantity is typed
  })

  it("renders desk panels: orders, alerts, book", async () => {
    renderWithQuery(<DeskScreen />)
    expect(await screen.findByText("Partially filled")).toBeInTheDocument()
    expect(await screen.findByText("Feed silent for 300s")).toBeInTheDocument()
    expect(await screen.findByText("SPY 2026-10-16 550 call")).toBeInTheDocument()
    expect(await screen.findByText("1 @ 1.25")).toBeInTheDocument()
  })
})
