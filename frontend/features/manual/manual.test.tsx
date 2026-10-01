import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { Sidebar, SidebarProvider } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DeskNav } from "@/features/desk/desk-nav"
import { LotDetail } from "@/features/lots/lot-detail"
import { EntryForm } from "@/features/manual/entry-form"
import { FlattenControl } from "@/features/manual/flatten-dialog"
import { KillSwitch } from "@/features/manual/kill-switch"
import { PositionDetail } from "@/features/positions/position-detail"
import { ProfileEditor } from "@/features/providers/profile-editor"
import { Toaster } from "@/components/toaster"
import { cardFixture, healthFixture, lotDetailFixture, positionDetailFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

vi.mock("next/navigation", () => ({ usePathname: () => "/" }))

const SNOWFLAKE = "111222333444555666"
const stepUpRequired = () =>
  HttpResponse.json({ code: "step_up_required", message: "confirm your password first" }, { status: 403 })

type Body = Record<string, unknown>
let bodies: Body[]
let fresh: boolean
let stepUps: number

/** Step-up is single-use: `guarded` consumes it, like the backend. */
function guarded(handler: (body: Body, attempt: number) => Response) {
  return async ({ request }: { request: Request }) => {
    const body = (await request.json()) as Body
    if (!fresh) return stepUpRequired()
    fresh = false
    bodies.push(body)
    return handler(body, bodies.length)
  }
}

function recorded(response: () => Response) {
  return async ({ request }: { request: Request }) => {
    bodies.push((await request.json()) as Body)
    return response()
  }
}

beforeEach(() => {
  bodies = []
  fresh = false
  stepUps = 0
  server.use(
    http.post("*/api/v1/auth/step-up", () => {
      fresh = true
      stepUps += 1
      return HttpResponse.json({ stepUpUntil: "2026-09-29T15:00:00Z" })
    }),
  )
})

async function confirmPassword() {
  await userEvent.type(await screen.findByLabelText("Password"), "correct horse battery")
  await userEvent.click(screen.getByRole("button", { name: "Confirm" }))
}

async function fillStockEntry() {
  await userEvent.selectOptions(screen.getByLabelText("Instrument"), "stock")
  await userEvent.type(screen.getByLabelText("Underlying"), "spy")
  await userEvent.type(screen.getByLabelText("Limit price"), "1.25")
}

describe("manual entry", () => {
  it("asks for the password first, keeps the idempotency key across it, and rotates it after a 422", async () => {
    server.use(
      http.post(
        "*/api/v1/manual/entry",
        guarded((_body, attempt) =>
          attempt === 1
            ? HttpResponse.json({ code: "invalid", message: "entries are blocked" }, { status: 422 })
            : HttpResponse.json({ cardId: cardFixture.id, state: "approved", orderIds: [] }),
        ),
      ),
    )
    renderWithQuery(<EntryForm />)
    await fillStockEntry()

    await userEvent.click(screen.getByRole("button", { name: "Submit entry" }))
    await confirmPassword()
    expect(await screen.findByRole("alert")).toHaveTextContent("entries are blocked")
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({ instrument: { instrument: "stock", underlying: "SPY" }, qty: 1, limitPrice: "1.25" })

    await userEvent.click(screen.getByRole("button", { name: "Submit entry" }))
    await confirmPassword()
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(stepUps).toBe(2)
    expect(typeof bodies[0].idempotencyKey).toBe("string")
    expect(bodies[1].idempotencyKey).not.toBe(bodies[0].idempotencyKey)
  })

  it("sends the same key on both calls of one submission (403 then retry)", async () => {
    const keys: unknown[] = []
    server.use(
      http.post("*/api/v1/manual/entry", async ({ request }) => {
        keys.push(((await request.json()) as Body).idempotencyKey)
        return fresh ? HttpResponse.json({ cardId: cardFixture.id, state: "approved", orderIds: [] }) : stepUpRequired()
      }),
    )
    renderWithQuery(<EntryForm />)
    await fillStockEntry()
    await userEvent.click(screen.getByRole("button", { name: "Submit entry" }))
    await confirmPassword()
    await waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBe(keys[1])
  })
})

describe("flatten", () => {
  it("picks a scope, confirms with the password and shows each blocked lot", async () => {
    server.use(
      http.post(
        "*/api/v1/flatten",
        guarded(() =>
          HttpResponse.json({
            killSwitch: true,
            lots: [
              { cardId: cardFixture.id, state: "approved", orderIds: ["o1"], lotId: lotDetailFixture.id, blocked: null },
              { cardId: "0a1b2c3d-0000-4000-8000-000000000002", state: "rejected", orderIds: [], lotId: "lot-2", blocked: "market_closed" },
            ],
          }),
        ),
      ),
    )
    renderWithQuery(<FlattenControl />)
    await userEvent.click(screen.getByRole("button", { name: "Flatten" }))
    const dialog = screen.getByRole("dialog", { name: "Flatten positions" })
    await userEvent.click(within(dialog).getByRole("radio", { name: "Everything" }))
    await userEvent.click(within(dialog).getByRole("button", { name: "Flatten now" }))
    await confirmPassword()
    expect(await screen.findByText("Blocked: Market closed")).toBeInTheDocument()
    expect(bodies[0]).toMatchObject({ scope: "all" })
    expect(typeof bodies[0].idempotencyKey).toBe("string")
  })
})

describe("lot actions", () => {
  it("adjusts to the broker quantity behind a password check", async () => {
    server.use(http.post(`*/api/v1/lots/${lotDetailFixture.id}/adjust`, guarded(() => HttpResponse.json({ ...lotDetailFixture, qty: 3 }))))
    renderWithQuery(<LotDetail lotId={lotDetailFixture.id} />)
    const form = within(await screen.findByRole("form", { name: "Adjust" }))
    await userEvent.clear(form.getByLabelText("Broker qty"))
    await userEvent.type(form.getByLabelText("Broker qty"), "3")
    expect(form.getByRole("button", { name: "Adjust quantity" })).toBeDisabled()
    await userEvent.type(form.getByLabelText("Adjust reason"), "broker shows 3")
    await userEvent.click(form.getByRole("button", { name: "Adjust quantity" }))
    await confirmPassword()
    await waitFor(() => expect(bodies).toEqual([{ qty: 3, reason: "broker shows 3" }]))
  })

  it("exits without a password and adds a stop", async () => {
    server.use(
      http.post("*/api/v1/manual/exit", recorded(() => HttpResponse.json({ cardId: cardFixture.id, state: "approved", orderIds: [] }))),
      http.post("*/api/v1/manual/stops", recorded(() => HttpResponse.json({ cardId: cardFixture.id, state: "approved", orderIds: [] }))),
    )
    renderWithQuery(<LotDetail lotId={lotDetailFixture.id} />)
    await userEvent.click(within(await screen.findByRole("form", { name: "Exit" })).getByRole("button", { name: "Exit" }))
    const stop = within(screen.getByRole("form", { name: "Stop" }))
    await userEvent.type(stop.getByLabelText("Stop level"), "0.8")
    await userEvent.click(stop.getByRole("button", { name: "Submit stop change" }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[0]).toMatchObject({ lotId: lotDetailFixture.id, qty: lotDetailFixture.qty })
    expect(bodies[1]).toMatchObject({ lotId: lotDetailFixture.id, op: "add", level: "0.8", basis: "premium" })
    expect(stepUps).toBe(0)
  })

  it("offers exit only on a foreign lot", async () => {
    server.use(http.get("*/api/v1/lots/:id", () => HttpResponse.json({ ...lotDetailFixture, isForeign: true })))
    renderWithQuery(<LotDetail lotId={lotDetailFixture.id} />)
    expect(await screen.findByRole("form", { name: "Exit" })).toBeInTheDocument()
    expect(screen.queryByRole("form", { name: "Stop" })).not.toBeInTheDocument()
    expect(screen.queryByRole("form", { name: "Adjust" })).not.toBeInTheDocument()
  })
})

describe("kill switch", () => {
  it("needs a reason before it turns on, then posts { active, reason }", async () => {
    server.use(http.post("*/api/v1/kill-switch", recorded(() => HttpResponse.json({ active: true }))))
    renderWithQuery(
      <>
        <KillSwitch />
        <Toaster />
      </>,
    )
    await userEvent.click(await screen.findByRole("button", { name: "Kill switch" }))
    const dialog = screen.getByRole("dialog", { name: "Turn kill switch on" })
    const confirm = within(dialog).getByRole("button", { name: "Turn on" })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Reason"), "  broker outage ")
    await userEvent.click(confirm)
    await waitFor(() => expect(bodies).toEqual([{ active: true, reason: "broker outage" }]))
    expect(await screen.findByText("Kill switch is on")).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })

  it("offers to turn it off when the flag is set", async () => {
    server.use(http.get("*/api/v1/health/details", () => HttpResponse.json({ ...healthFixture, flags: { kill_switch: true } })))
    renderWithQuery(<KillSwitch />)
    await userEvent.click(await screen.findByRole("button", { name: "Kill switch on: turn off" }))
    expect(screen.getByRole("dialog", { name: "Turn kill switch off" })).toBeInTheDocument()
  })

  it("has a Manual entry link in the desk nav", () => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    renderWithQuery(
      <TooltipProvider>
        <SidebarProvider>
          <DeskNav />
        </SidebarProvider>
      </TooltipProvider>,
    )
    expect(screen.getByRole("link", { name: "Manual entry" })).toHaveAttribute("href", "/manual/entry")
    vi.unstubAllGlobals()
  })
})

describe("sidebar shortcut", () => {
  it("toggles from the page and leaves text fields alone", async () => {
    render(
      <TooltipProvider>
        <SidebarProvider>
          <Sidebar>
            <label>
              Reason
              <textarea />
            </label>
          </Sidebar>
        </SidebarProvider>
      </TooltipProvider>,
    )
    const sidebar = document.querySelector("[data-slot=sidebar]")
    expect(sidebar).toHaveAttribute("data-state", "expanded")

    const typed = new KeyboardEvent("keydown", { key: "b", ctrlKey: true, bubbles: true, cancelable: true })
    screen.getByRole("textbox").dispatchEvent(typed)
    expect(typed.defaultPrevented).toBe(false)
    expect(sidebar).toHaveAttribute("data-state", "expanded")

    const global = new KeyboardEvent("keydown", { key: "b", metaKey: true, bubbles: true, cancelable: true })
    await act(async () => {
      window.dispatchEvent(global)
    })
    expect(global.defaultPrevented).toBe(true)
    expect(sidebar).toHaveAttribute("data-state", "collapsed")
    document.cookie = "sidebar_state=; path=/; max-age=0"
  })
})

describe("position actions", () => {
  it("closes a position with a reason", async () => {
    server.use(http.patch("*/api/v1/positions/:id", recorded(() => HttpResponse.json(positionDetailFixture))))
    renderWithQuery(<PositionDetail positionId={positionDetailFixture.id} />)
    await userEvent.click(await screen.findByRole("button", { name: "Close position" }))
    const dialog = screen.getByRole("dialog", { name: "Close position" })
    await userEvent.type(within(dialog).getByLabelText("Reason"), "provider said closed")
    await userEvent.click(within(dialog).getByRole("button", { name: "Close position" }))
    await waitFor(() => expect(bodies).toEqual([{ status: "closed", reason: "provider said closed" }]))
  })

  it("adds a position with the provider id kept as an exact string", async () => {
    server.use(
      http.get("*/api/v1/positions/:id", () => HttpResponse.json({ ...positionDetailFixture, providerId: SNOWFLAKE })),
      http.post("*/api/v1/positions", recorded(() => HttpResponse.json(positionDetailFixture))),
    )
    renderWithQuery(<PositionDetail positionId={positionDetailFixture.id} />)
    await userEvent.click(await screen.findByRole("button", { name: "Add position" }))
    const dialog = screen.getByRole("dialog", { name: "Add position" })
    await userEvent.type(within(dialog).getByLabelText("Underlying"), "qqq")
    fireEvent.change(within(dialog).getByLabelText("Expiry"), { target: { value: "2026-10-16" } })
    await userEvent.type(within(dialog).getByLabelText("Strike"), "480")
    await userEvent.type(within(dialog).getByLabelText("Reason"), "missed by parser")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      providerId: SNOWFLAKE,
      instrument: "option",
      underlying: "QQQ",
      expiry: "2026-10-16",
      strike: "480",
      right: "call",
      reason: "missed by parser",
    })
  })

  it("re-links one of the provider's signals to the position", async () => {
    server.use(http.post("*/api/v1/links", recorded(() => HttpResponse.json({ cardIds: [] }))))
    renderWithQuery(<PositionDetail positionId={positionDetailFixture.id} />)
    await userEvent.click(await screen.findByRole("button", { name: "Re-link signal" }))
    const dialog = screen.getByRole("dialog", { name: "Re-link signal" })
    await userEvent.click(await within(dialog).findByRole("radio"))
    await userEvent.type(within(dialog).getByLabelText("Reason"), "wrong reply chain")
    await userEvent.click(within(dialog).getByRole("button", { name: "Link signal" }))
    await waitFor(() =>
      expect(bodies).toEqual([
        { signalId: cardFixture.signalId, providerPositionId: positionDetailFixture.id, reason: "wrong reply chain" },
      ]),
    )
  })
})

describe("provider profile editor", () => {
  it("prefills from GET and saves only the lists that changed", async () => {
    let putUrl = ""
    server.use(
      http.put("*/api/v1/providers/:id/profile", async ({ request }) => {
        putUrl = new URL(request.url).pathname
        bodies.push((await request.json()) as Body)
        return HttpResponse.json({ aliases: ["spy", "spx"], lexicon: ["lotto", "runner"], habits: [] })
      }),
    )
    renderWithQuery(<ProfileEditor providerId={SNOWFLAKE} />)
    await userEvent.click(screen.getByRole("button", { name: "Provider profile" }))
    const aliases = await screen.findByLabelText("Aliases")
    expect(aliases).toHaveValue("spy\nspx")
    const lexicon = screen.getByLabelText("Lexicon")
    await userEvent.type(lexicon, "{Enter}runner")
    await userEvent.type(screen.getByLabelText("Reason"), "learned new term")
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(putUrl).toBe(`/api/v1/providers/${SNOWFLAKE}/profile`)
    expect(bodies[0]).toEqual({ lexicon: ["lotto", "runner"], reason: "learned new term" })
  })
})
