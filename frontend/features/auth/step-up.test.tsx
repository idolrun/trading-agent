import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { StepUpDialog } from "@/features/auth/step-up-dialog"
import { StepUpCancelledError, useStepUp } from "@/features/auth/use-step-up"
import { apiPost } from "@/lib/api/client"
import { server } from "@/test/msw"

const webauthn = vi.hoisted(() => ({ startAuthentication: vi.fn(), startRegistration: vi.fn() }))
vi.mock("@simplewebauthn/browser", () => webauthn)

let calls = 0
let outcome: "ok" | "always-step-up" = "ok"
let steps = 0

// Stands in for a protected mutation (e.g. manual entry in F059).
const protectedCall = () => apiPost<{ ok: true }>("/api/v1/test/protected")

function Harness({ onResult }: { onResult: (result: string) => void }) {
  const stepUp = useStepUp()
  const run = stepUp.withStepUp(protectedCall)
  return (
    <>
      <button
        onClick={() => run().then(() => onResult("done"), (e: Error) => onResult(e.name === "ApiError" ? "api-error" : e.name))}
      >
        act
      </button>
      <StepUpDialog {...stepUp.dialogProps} />
    </>
  )
}

beforeEach(() => {
  calls = 0
  steps = 0
  outcome = "ok"
  webauthn.startAuthentication.mockReset().mockResolvedValue({ id: "cred" })
  server.use(
    http.post("*/api/v1/test/protected", () => {
      calls += 1
      // First call needs step-up; later calls succeed once the step-up ran.
      if (outcome === "always-step-up" || steps === 0) {
        return HttpResponse.json({ code: "step_up_required", message: "confirm with your passkey first" }, { status: 403 })
      }
      return HttpResponse.json({ ok: true })
    }),
    http.post("*/api/v1/auth/step-up/options", () => HttpResponse.json({ challenge: "c" })),
    http.post("*/api/v1/auth/step-up/verify", () => {
      steps += 1
      return HttpResponse.json({ stepUpUntil: "2026-09-29T15:00:00Z" })
    }),
  )
})

describe("useStepUp", () => {
  it("confirms with a passkey on 403 step_up_required and retries once", async () => {
    const onResult = vi.fn()
    render(<Harness onResult={onResult} />)
    await userEvent.click(screen.getByText("act"))
    await userEvent.click(await screen.findByRole("button", { name: "Confirm with passkey" }))
    await vi.waitFor(() => expect(onResult).toHaveBeenCalledWith("done"))
    expect(calls).toBe(2)
    expect(steps).toBe(1)
    expect(webauthn.startAuthentication).toHaveBeenCalledWith({ optionsJSON: { challenge: "c" } })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("does not retry a second time when the retry still needs step-up", async () => {
    outcome = "always-step-up"
    const onResult = vi.fn()
    render(<Harness onResult={onResult} />)
    await userEvent.click(screen.getByText("act"))
    await userEvent.click(await screen.findByRole("button", { name: "Confirm with passkey" }))
    await vi.waitFor(() => expect(onResult).toHaveBeenCalledWith("api-error"))
    expect(calls).toBe(2)
    expect(steps).toBe(1)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("rejects without retrying when the dialog is cancelled", async () => {
    const onResult = vi.fn()
    render(<Harness onResult={onResult} />)
    await userEvent.click(screen.getByText("act"))
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
    await vi.waitFor(() => expect(onResult).toHaveBeenCalledWith(new StepUpCancelledError().name))
    expect(calls).toBe(1)
    expect(steps).toBe(0)
  })

  it("keeps the dialog open with a neutral message when the prompt is dismissed", async () => {
    webauthn.startAuthentication.mockRejectedValue(Object.assign(new Error("x"), { name: "NotAllowedError" }))
    render(<Harness onResult={vi.fn()} />)
    await userEvent.click(screen.getByText("act"))
    await userEvent.click(await screen.findByRole("button", { name: "Confirm with passkey" }))
    expect(await screen.findByRole("status")).toHaveTextContent("Passkey prompt closed")
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled()
  })

  it("does not intercept other errors", async () => {
    server.use(http.post("*/api/v1/test/protected", () => HttpResponse.json({ code: "invalid", message: "bad" }, { status: 422 })))
    const onResult = vi.fn()
    render(<Harness onResult={onResult} />)
    await userEvent.click(screen.getByText("act"))
    await vi.waitFor(() => expect(onResult).toHaveBeenCalledWith("api-error"))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
