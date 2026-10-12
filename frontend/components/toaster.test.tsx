import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { toast, Toaster } from "@/components/toaster"

describe("toast", () => {
  it("shows one toast when the same failure is reported twice", async () => {
    render(<Toaster />)
    toast("Couldn't save: the desk is busy")
    toast("Couldn't save: the desk is busy")
    expect(await screen.findByText("Couldn't save: the desk is busy")).toBeInTheDocument()
    expect(document.querySelectorAll("[data-sonner-toast]")).toHaveLength(1)
  })
})
