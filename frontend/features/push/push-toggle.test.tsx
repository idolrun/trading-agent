import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { PushToggle } from "@/features/push/push-toggle"
import { server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const SUB_ID = "0a1b2c3d-0000-4000-8000-0000000000e1"
const VAPID = "AQAB" // base64url for bytes [1, 0, 1]

const subscription = {
  toJSON: () => ({ endpoint: "https://push.example/1", keys: { p256dh: "key", auth: "secret" } }),
  unsubscribe: vi.fn(),
}
const pushManager = { getSubscription: vi.fn(), subscribe: vi.fn() }
const registration = { pushManager }
const requestPermission = vi.fn()

function browser({ permission = "default", existing = null as unknown } = {}) {
  pushManager.getSubscription.mockResolvedValue(existing)
  pushManager.subscribe.mockResolvedValue(subscription)
  subscription.unsubscribe.mockResolvedValue(true)
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { register: vi.fn().mockResolvedValue(registration), ready: Promise.resolve(registration) },
  })
  vi.stubGlobal("PushManager", class {})
  vi.stubGlobal("Notification", Object.assign(class {}, { permission, requestPermission }))
}

let posted: unknown[]
let deleted: string[]

beforeEach(() => {
  posted = []
  deleted = []
  pushManager.getSubscription.mockReset()
  pushManager.subscribe.mockReset()
  requestPermission.mockReset()
  vi.stubGlobal("matchMedia", () => ({ matches: false }))
  server.use(
    http.get("*/api/v1/push/vapid-public-key", () => HttpResponse.json({ publicKey: VAPID })),
    http.post("*/api/v1/push/subscriptions", async ({ request }) => {
      posted.push(await request.json())
      return HttpResponse.json({ subscriptionId: SUB_ID }, { status: 201 })
    }),
    http.delete("*/api/v1/push/subscriptions/:id", ({ params }) => {
      deleted.push(String(params.id))
      return new HttpResponse(null, { status: 204 })
    }),
  )
})
afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker")
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("PushToggle", () => {
  it("registers the worker, then subscribes and stores the subscription on click", async () => {
    browser()
    requestPermission.mockResolvedValue("granted")
    renderWithQuery(<PushToggle />)

    await userEvent.click(await screen.findByRole("switch", { name: "Enable notifications" }))

    expect(await screen.findByRole("switch", { name: "Disable notifications" })).toBeInTheDocument()
    expect(navigator.serviceWorker.register).toHaveBeenCalledWith("/sw.js", { scope: "/", updateViaCache: "none" })
    expect(pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: new Uint8Array([1, 0, 1]),
    })
    expect(posted).toEqual([{ endpoint: "https://push.example/1", keys: { p256Dh: "key", auth: "secret" } }])
  })

  it("starts enabled when the browser already has a subscription, and disables it", async () => {
    browser({ permission: "granted", existing: subscription })
    renderWithQuery(<PushToggle />)

    await userEvent.click(await screen.findByRole("switch", { name: "Disable notifications" }))

    expect(await screen.findByRole("switch", { name: "Enable notifications" })).toBeInTheDocument()
    expect(deleted).toEqual([SUB_ID])
    expect(subscription.unsubscribe).toHaveBeenCalled()
  })

  it("does not subscribe when permission is refused", async () => {
    browser()
    requestPermission.mockResolvedValue("denied")
    renderWithQuery(<PushToggle />)

    await userEvent.click(await screen.findByRole("switch", { name: "Enable notifications" }))

    expect(await screen.findByText(/blocked/i)).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
    expect(pushManager.subscribe).not.toHaveBeenCalled()
  })

  it("shows blocked when permission was denied earlier", async () => {
    browser({ permission: "denied" })
    renderWithQuery(<PushToggle />)
    expect(await screen.findByText(/blocked/i)).toBeInTheDocument()
  })

  it("drops the browser subscription when the server rejects it", async () => {
    browser()
    requestPermission.mockResolvedValue("granted")
    server.use(
      http.post("*/api/v1/push/subscriptions", () =>
        HttpResponse.json({ code: "internal", message: "boom" }, { status: 500 }),
      ),
    )
    renderWithQuery(<PushToggle />)

    await userEvent.click(await screen.findByRole("switch", { name: "Enable notifications" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("boom")
    expect(subscription.unsubscribe).toHaveBeenCalled()
    expect(screen.getByRole("switch", { name: "Enable notifications" })).toBeInTheDocument()
  })

  it("says so when the browser has no push support", async () => {
    renderWithQuery(<PushToggle />)
    expect(await screen.findByText(/not supported/i)).toBeInTheDocument()
  })

  it("tells iOS Safari users to add the app to the Home Screen first", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
    renderWithQuery(<PushToggle />)
    await waitFor(() => expect(screen.getByText(/Add to Home Screen/)).toBeInTheDocument())
  })
})
